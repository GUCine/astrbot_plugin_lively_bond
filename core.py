"""機器人的關係狀態：熟悉度、好感、心情、印象、人物卡、上次見面，以及機器人自己此刻的狀態。

純邏輯 + SQLite，不依賴 AstrBot，方便單獨測試。
"""
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
DAY = 86400.0

OWNERS: set[str] = set()  # 主人的 QQ，插件加載時從配置填進來

# 好感：-50 ~ 100，新認識的人從 5 開始
FAVOR_MIN, FAVOR_MAX, FAVOR_START = -50.0, 100.0, 5.0
FAVOR_UP_MAX, FAVOR_DOWN_MAX = 3, 5  # 單次最多 +3 / -5
FAVOR_DAY_CAP = 5.0  # 每人每天最多加 5
FAVOR_HALF_AFTER = 3.0  # 當天已加滿 3 後，再加的減半
FAVOR_IDLE_DAYS = 3  # 超過 3 天沒見到，好感開始回落
FAVOR_BASELINE = 10.0  # 回落到這裡為止（低於它的會慢慢回升到這裡）

# 熟悉度：0 ~ 100，每天看到 +1，和機器人說話再 +2
FAM_MAX = 100.0
FAM_SEEN, FAM_TALK = 1.0, 2.0
FAM_IDLE_DAYS = 30  # 一個月不見開始回落
FAM_DECAY_EVERY = 7  # 之後每 7 天 -1
FAM_FLOOR = 6.0  # 最低迴到「眼熟」，不會變回陌生

WARM_CAP = 30.0  # 常來聊天、沒鬧矛盾的人，每天自然熟絡一點，漲到這裡為止（再往上要靠真的投緣）
WARM_STEP = 0.5

# 心情：對這個人的短期情緒，強度 1～3，同一種心情再被觸發會加深；按種類不同，每隔一段時間弱一級，弱到 0 就回到平常
MOODS = ("开心", "得意", "感动", "害羞", "吃醋", "委屈", "小生气", "担心", "嫌弃", "平常")
POSITIVE = {"开心", "得意", "感动", "害羞"}
NEGATIVE = {"吃醋", "委屈", "小生气", "嫌弃"}
MOOD_LEVEL, MOOD_MAX = 2, 3
MOOD_FADE = {  # 每弱一級要多久：氣來得快去得快，擔心和感動留得久
    "开心": 90 * 60, "得意": 60 * 60, "感动": 120 * 60, "害羞": 60 * 60, "吃醋": 60 * 60,
    "委屈": 90 * 60, "小生气": 60 * 60, "担心": 180 * 60, "嫌弃": 45 * 60,
}
MOOD_ALIASES = {
    "高兴": "开心", "快乐": "开心", "愉快": "开心", "兴奋": "开心", "骄傲": "得意", "嘚瑟": "得意", "臭美": "得意",
    "感激": "感动", "温暖": "感动", "不好意思": "害羞", "脸红": "害羞", "酸": "吃醋", "醋": "吃醋",
    "难过": "委屈", "伤心": "委屈", "失落": "委屈", "生气": "小生气", "气": "小生气", "哼": "小生气",
    "担忧": "担心", "心疼": "担心", "无语": "嫌弃", "鄙视": "嫌弃", "无奈": "嫌弃", "普通": "平常",
}

MISS_DAYS = 3  # 隔這麼多天再出現，算「好久不見」
MISS_VALID = 6 * 3600  # 好久不見的提示在對方回來後 6 小時內有效

IMPRESSION_MAX = 20
CARD_MAX = 160  # 人物卡注入時最多這麼多字

# 模型偶爾把結尾寫錯（如「>::」）或漏寫，所以沒閉合時刪到這一行結尾；也認全角括號
OPEN = r"(?:<<|《《|＜＜)"
CLOSE = r"(?:>>|》》|＞＞)"
# 模型有時把「|印象:…」寫在 >> 外面（同一行或下一行），一起算作標記
TRAIL = r"((?:[ \t]*\n?[ \t]*[|｜][^\n]*)?)"
TAG_RE = re.compile(OPEN + r"\s*好感\s*([+\-－]?\s*\d+)?([^\n]*?)(?:" + CLOSE + r"|$)" + TRAIL, re.M)
ANY_TAG_RE = re.compile(OPEN + r"[^\n]*?(?:" + CLOSE + r"|$)" + TRAIL, re.M)
# 前半截已經沒了、只剩「|印象:…」「|心情:…」的殘片
ORPHAN_RE = re.compile(r"[ \t]*[|｜]\s*(?:印象|心情)\s*[:：][^\n]*")
TAIL_JUNK = re.compile(r"[\s>＞》:：]+$")


def norm_mood(word: str) -> str | None:
    word = (word or "").strip()
    if word in MOODS:
        return word
    return MOOD_ALIASES.get(word)


def today(now: float) -> str:
    return datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")


def parse_tag(text: str):
    """從回覆裡取出標記並刪掉。返回 (乾淨文本, 解析結果或 None)。"""
    if not has_tag(text):
        return text, None
    found = None
    m = None
    for m in TAG_RE.finditer(text):
        pass
    if m:
        raw_delta = (m.group(1) or "0").replace("－", "-").replace(" ", "")
        try:
            delta = int(raw_delta)
        except ValueError:
            delta = 0
        mood, impression = None, None
        rest = (m.group(2) or "") + "|" + (m.group(3) or "")
        rest += "|" + "|".join(ORPHAN_RE.findall(text))
        for seg in re.split(r"[|｜]", rest):
            seg = TAIL_JUNK.sub("", seg.strip())
            if not seg:
                continue
            if seg.startswith(("印象:", "印象：")):
                impression = seg[3:].strip()[:IMPRESSION_MAX] or None
            elif seg.startswith(("心情:", "心情：")):
                mood = norm_mood(seg[3:]) or mood
            elif norm_mood(seg):
                mood = norm_mood(seg)
        found = {"delta": delta, "mood": mood, "impression": impression}
    clean = ORPHAN_RE.sub("", ANY_TAG_RE.sub("", text))
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{2,}", "\n", clean).strip()
    return clean, found


def has_tag(text: str) -> bool:
    return bool(text) and bool(re.search(OPEN, text) or ORPHAN_RE.search(text))


def strip_tags(text: str) -> str:
    if not has_tag(text):
        return text
    return parse_tag(text)[0]


class BondStore:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                qq TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                familiarity REAL DEFAULT 0,
                favor REAL DEFAULT 5,
                mood TEXT DEFAULT '平常',
                mood_level INTEGER DEFAULT 0,
                mood_at REAL DEFAULT 0,
                impression TEXT DEFAULT '',
                first_met REAL DEFAULT 0,
                last_seen REAL DEFAULT 0,
                last_talk REAL DEFAULT 0,
                seen_day TEXT DEFAULT '',
                talk_day TEXT DEFAULT '',
                gain_day TEXT DEFAULT '',
                day_gain REAL DEFAULT 0,
                favor_decay_at REAL DEFAULT 0,
                fam_decay_at REAL DEFAULT 0,
                miss_days INTEGER DEFAULT 0,
                miss_at REAL DEFAULT 0,
                talks INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq TEXT, ts REAL, kind TEXT, delta REAL, after REAL,
                note TEXT DEFAULT '', source TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_events_qq ON events(qq, ts);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS cards (qq TEXT PRIMARY KEY, card TEXT, updated REAL);
            """
        )
        self.db.commit()

    # ---------- 基礎讀寫 ----------
    def get_meta(self, k: str, default: str = "") -> str:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k: str, v: str):
        self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))
        self.db.commit()

    def get_card(self, qq: str) -> dict | None:
        r = self.db.execute("SELECT card, updated FROM cards WHERE qq=?", (qq,)).fetchone()
        if not r:
            return None
        try:
            return dict(json.loads(r["card"]), _updated=r["updated"])
        except Exception:
            return None

    def set_card(self, qq: str, card: dict, now: float | None = None):
        self.db.execute("INSERT OR REPLACE INTO cards(qq, card, updated) VALUES(?,?,?)",
                        (qq, json.dumps(card, ensure_ascii=False), now or time.time()))
        self.db.commit()

    def get(self, qq: str):
        r = self.db.execute("SELECT * FROM people WHERE qq=?", (qq,)).fetchone()
        return dict(r) if r else None

    def _new(self, qq: str, name: str, now: float) -> dict:
        p = {
            "qq": qq, "name": name or "", "familiarity": FAM_MAX if qq in OWNERS else 0.0,
            "favor": FAVOR_MAX if qq in OWNERS else FAVOR_START,
            "mood": "平常", "mood_level": 0, "mood_at": now, "impression": "",
            "first_met": now, "last_seen": 0.0, "last_talk": 0.0,
            "seen_day": "", "talk_day": "", "gain_day": "", "day_gain": 0.0,
            "favor_decay_at": now, "fam_decay_at": now,
            "miss_days": 0, "miss_at": 0.0, "talks": 0,
        }
        cols = ",".join(p)
        self.db.execute(
            f"INSERT INTO people({cols}) VALUES({','.join('?' * len(p))})",
            tuple(p.values()),
        )
        return p

    def save(self, p: dict):
        cols = [k for k in p if k != "qq"]
        self.db.execute(
            f"UPDATE people SET {','.join(c + '=?' for c in cols)} WHERE qq=?",
            (*[p[c] for c in cols], p["qq"]),
        )
        self.db.commit()

    def log(self, qq, kind, delta, after, note="", source="", now=None):
        self.db.execute(
            "INSERT INTO events(qq,ts,kind,delta,after,note,source) VALUES(?,?,?,?,?,?,?)",
            (qq, now or time.time(), kind, delta, after, note, source),
        )

    # ---------- 隨時間變化（讀取時順帶結算） ----------
    def settle(self, p: dict, now: float):
        if p["qq"] in OWNERS:
            # 主人：好感、熟悉度都固定滿分，不隨時間變化
            p["favor"] = FAVOR_MAX
            p["familiarity"] = FAM_MAX
        else:
            # 好感：超過 3 天沒見到，每天向 10 靠攏 1 點
            idle_from = max(p["last_seen"] + FAVOR_IDLE_DAYS * DAY, p["favor_decay_at"])
            if p["last_seen"] and now > idle_from:
                days = int((now - idle_from) // DAY)
                if days > 0:
                    f = p["favor"]
                    if f > FAVOR_BASELINE:
                        f = max(FAVOR_BASELINE, f - days)
                    elif f < FAVOR_BASELINE:
                        f = min(FAVOR_BASELINE, f + days)
                    if f != p["favor"]:
                        self.log(p["qq"], "favor_decay", f - p["favor"], f, f"{days}天没见", now=now)
                    p["favor"] = f
                    p["favor_decay_at"] = idle_from + days * DAY
        # 熟悉度：一個月不見後每 7 天 -1，最低到眼熟
        fam_from = max(p["last_seen"] + FAM_IDLE_DAYS * DAY, p["fam_decay_at"])
        if p["qq"] not in OWNERS and p["last_seen"] and now > fam_from:
            steps = int((now - fam_from) // (FAM_DECAY_EVERY * DAY))
            if steps > 0:
                if p["familiarity"] > FAM_FLOOR:
                    p["familiarity"] = max(FAM_FLOOR, p["familiarity"] - steps)
                p["fam_decay_at"] = fam_from + steps * FAM_DECAY_EVERY * DAY
        # 心情：隨時間消退
        if p["mood_level"] > 0 and p["mood_at"]:
            fades = int((now - p["mood_at"]) // MOOD_FADE.get(p["mood"], 90 * 60))
            if fades > 0:
                p["mood_level"] = max(0, p["mood_level"] - fades)
                p["mood_at"] += fades * MOOD_FADE.get(p["mood"], 90 * 60)
                if p["mood_level"] == 0:
                    p["mood"] = "平常"
        return p

    def load(self, qq: str, name: str = "", now: float | None = None) -> dict:
        now = now or time.time()
        p = self.get(qq) or self._new(qq, name, now)
        # 有的消息拿不到暱稱，AstrBot 會用 QQ 號頂上；這種不要蓋掉已經知道的名字
        if name and not (name == qq and p.get("name") and p["name"] != qq):
            p["name"] = name
        return self.settle(p, now)

    # ---------- 事件 ----------
    def on_seen(self, qq: str, name: str, now: float | None = None) -> dict:
        """看到這個人發消息（群裡或私聊）。"""
        now = now or time.time()
        p = self.load(qq, name, now)
        if p["last_seen"] and now - p["last_seen"] >= MISS_DAYS * DAY:
            p["miss_days"] = int((now - p["last_seen"]) // DAY)
            p["miss_at"] = now
        d = today(now)
        if p["seen_day"] != d:
            p["seen_day"] = d
            p["familiarity"] = min(FAM_MAX, p["familiarity"] + FAM_SEEN)
        p["last_seen"] = now
        self.save(p)
        return p

    def on_talk(self, qq: str, name: str, now: float | None = None) -> dict:
        """這個人在和機器人說話（即將生成回覆）。返回結算後的狀態。"""
        now = now or time.time()
        p = self.load(qq, name, now)
        d = today(now)
        if p["talk_day"] != d:
            p["talk_day"] = d
            p["familiarity"] = min(FAM_MAX, p["familiarity"] + FAM_TALK)
            # 常來聊天又沒鬧矛盾，關係自然會熟絡一點
            if qq not in OWNERS and p["favor"] < WARM_CAP and not self.db.execute(
                    "SELECT 1 FROM events WHERE qq=? AND kind='favor' AND delta<0 AND ts>?",
                    (qq, now - DAY)).fetchone():
                p["favor"] = min(WARM_CAP, p["favor"] + WARM_STEP)
                self.log(qq, "favor", WARM_STEP, p["favor"], "常来聊天", "warm", now)
        p["last_talk"] = now
        p["last_seen"] = max(p["last_seen"], now)
        p["talks"] += 1
        self.save(p)
        return p

    def apply_tag(self, qq: str, name: str, tag: dict, source: str, now: float | None = None) -> dict:
        now = now or time.time()
        p = self.load(qq, name, now)
        delta = max(-FAVOR_DOWN_MAX, min(FAVOR_UP_MAX, int(tag.get("delta") or 0)))
        if qq not in OWNERS and delta:
            d = today(now)
            if p["gain_day"] != d:
                p["gain_day"], p["day_gain"] = d, 0.0
            if delta > 0:
                eff = float(delta)
                if p["day_gain"] >= FAVOR_HALF_AFTER:
                    eff = eff / 2
                eff = min(eff, max(0.0, FAVOR_DAY_CAP - p["day_gain"]))
                p["day_gain"] += eff
            else:
                eff = float(delta)
            if eff:
                p["favor"] = max(FAVOR_MIN, min(FAVOR_MAX, p["favor"] + eff))
                self.log(qq, "favor", eff, p["favor"], f"模型给{delta:+d}", source, now)
        mood = tag.get("mood")
        if mood and mood != "平常":
            if mood != p["mood"] or p["mood_level"] <= 0:
                self.log(qq, "mood", 0, 0, mood, source, now)
                p["mood_level"] = MOOD_LEVEL
            else:
                p["mood_level"] = min(MOOD_MAX, p["mood_level"] + 1)  # 同一種心情又被戳到，更強烈
            p["mood"], p["mood_at"] = mood, now
        elif mood == "平常" and p["mood_level"] > 0:
            p["mood_level"] -= 1
            p["mood_at"] = now
            if p["mood_level"] == 0:
                p["mood"] = "平常"
        imp = tag.get("impression")
        if imp and imp != p["impression"]:
            p["impression"] = imp
            self.log(qq, "impression", 0, 0, imp, source, now)
        # 好久不見只提醒一次
        p["miss_days"], p["miss_at"] = 0, 0.0
        self.save(p)
        return p

    def admin_set(self, qq: str, field: str, value, now: float | None = None) -> dict:
        now = now or time.time()
        p = self.load(qq, "", now)
        if field == "favor":
            value = max(FAVOR_MIN, min(FAVOR_MAX, float(value)))
            self.log(qq, "admin", value - p["favor"], value, "主人修改好感", "admin", now)
            p["favor"] = value
        elif field == "familiarity":
            value = max(0.0, min(FAM_MAX, float(value)))
            self.log(qq, "admin", value - p["familiarity"], value, "主人修改熟悉度", "admin", now)
            p["familiarity"] = value
        elif field == "impression":
            p["impression"] = (value or "")[:IMPRESSION_MAX]
            self.log(qq, "admin", 0, 0, f"主人修改印象:{p['impression']}", "admin", now)
        elif field == "mood":
            p["mood"] = value
            p["mood_level"] = 0 if value == "平常" else MOOD_LEVEL
            p["mood_at"] = now
            self.log(qq, "admin", 0, 0, f"主人修改心情:{value}", "admin", now)
        self.save(p)
        return p

    def reset(self, qq: str):
        self.db.execute("DELETE FROM people WHERE qq=?", (qq,))
        self.log(qq, "admin", 0, 0, "主人重置", "admin")
        self.db.commit()

    def self_state(self, now: float | None = None) -> str:
        """機器人自己此刻的狀態：看時間和最近三小時大家讓她有什麼心情"""
        now = now or time.time()
        hm = datetime.fromtimestamp(now, TZ).strftime("%H:%M")
        bits = []
        if "06:00" <= hm < "09:00":
            bits.append("刚睡醒，还有点迷糊")
        elif "11:30" <= hm < "12:30" or "17:30" <= hm < "18:30":
            bits.append("到饭点了，有点饿，惦记着白饭")
        elif hm >= "23:30" or hm < "06:00":
            bits.append("很晚了，有点困")
        rows = self.db.execute("SELECT qq, note FROM events WHERE kind='mood' AND ts>?", (now - 3 * 3600,)).fetchall()
        pos = sum(1 for r in rows if r["note"] in POSITIVE)
        neg = sum(1 for r in rows if r["note"] in NEGATIVE)
        owner_pos = any(r["qq"] in OWNERS and r["note"] in POSITIVE for r in rows)
        if owner_pos:
            bits.append("刚被〈主人〉陪过，元气满满")
        if pos >= 4 and pos >= 2 * max(neg, 1):
            bits.append("这会儿被大家逗得很开心")
        elif neg >= 3 and neg > pos:
            bits.append("刚才被惹得有点闷闷不乐，不过不会迁怒别人")
        elif pos >= 2:
            bits.append("心情不错")
        last = self.db.execute("SELECT MAX(last_talk) m FROM people").fetchone()["m"] or 0
        if "09:00" <= hm < "23:00" and now - last > 3 * 3600:
            bits.append("好一阵没人找〈我〉说话了，有点无聊")
        text = "，".join(bits) or "平平常常"
        self.set_meta("self_state", json.dumps({"text": text, "ts": now, "pos": pos, "neg": neg}, ensure_ascii=False))
        return text

    def recent(self, limit: int = 15):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM people ORDER BY last_seen DESC LIMIT ?", (limit,))]

    def events(self, qq: str, limit: int = 6):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM events WHERE qq=? ORDER BY ts DESC LIMIT ?", (qq, limit))]


# ---------- 給模型看的描述（不出現數字） ----------
def stage(p: dict) -> str:
    if p["qq"] in OWNERS:
        return "主人，最亲的人，〈我〉叫 TA「〈主人〉」"
    fam, fav = p["familiarity"], p["favor"]
    if fav <= -20:
        return "有点讨厌的人"
    if fam < 6:
        return "还不太认识的人"
    if fam < 20:
        return "眼熟的群友"
    if fav >= 65 and fam >= 70:
        return "很亲近的人"
    if fav >= 30 and fam >= 45:
        return "好朋友"
    return "熟人"


def favor_desc(p: dict) -> str:
    if p["qq"] in OWNERS:
        return "最喜欢"
    f = p["favor"]
    if f >= 65:
        return "很喜欢 TA"
    if f >= 30:
        return "挺喜欢 TA"
    if f >= 0:
        return "感觉普通"
    if f > -20:
        return "有点不爽 TA"
    return "挺讨厌 TA"


MOOD_SHOW = {  # 心情 -> 說話時的樣子
    "开心": "开心（话多一点、语气轻快）",
    "得意": "得意（被夸得尾巴翘起来，会小小炫耀）",
    "感动": "感动（软软的，会认真道谢）",
    "害羞": "害羞（说话结巴、想转移话题）",
    "吃醋": "在吃醋（酸溜溜地阴阳一句，但不是真生气）",
    "委屈": "委屈（声音小小的，想被哄）",
    "担心": "担心 TA（会多问一句、叮嘱 TA）",
    "嫌弃": "嫌弃（翻白眼式吐槽，傲娇）",
}


def mood_desc(p: dict) -> str:
    if p["mood_level"] <= 0 or p["mood"] == "平常":
        return "平常"
    lv = p["mood_level"]
    if p["mood"] == "小生气":
        return {1: "还有一点点气", 2: "在生 TA 的气（傲娇，嘴硬心软，照样回）"}.get(lv, "很生 TA 的气（短句、带刺，但不骂人，哄一哄就好）")
    show = MOOD_SHOW.get(p["mood"])
    if not show:
        return "平常"
    return ("有一点" if lv == 1 else "特别" if lv >= 3 else "") + show


def card_text(card: dict | None) -> str:
    if not card:
        return ""
    bits = []
    if card.get("称呼"):
        bits.append(f"平时叫 TA「{card['称呼']}」")
    for k, label in (("喜好", "喜欢"), ("雷区", "别碰"), ("梗", "你们的梗"), ("最近", "TA 最近")):
        v = card.get(k)
        if isinstance(v, list):
            v = "、".join(str(x) for x in v[:4] if x)
        if v:
            bits.append(f"{label}：{v}")
    if card.get("相处"):
        bits.append(str(card["相处"]))
    return "；".join(bits)[:CARD_MAX]


def state_line(p: dict, now: float | None = None, card: dict | None = None) -> str:
    now = now or time.time()
    name = p["name"] or "TA"
    parts = [f"你和正在说话的「{name}」：{stage(p)}，{favor_desc(p)}"]
    if p["impression"]:
        parts.append(f"你对 TA 的印象：{p['impression']}")
    if card_text(card):
        parts.append(f"你记得 TA：{card_text(card)}")
    md = mood_desc(p)
    parts.append(f"你现在对 TA 的心情：{md}" + ("（想发表情包就挑合这个心情的）" if md != "平常" else ""))
    if p["miss_days"] >= MISS_DAYS and now - p["miss_at"] <= MISS_VALID:
        parts.append(f"已经 {p['miss_days']} 天没见到 TA 了，开口可以自然地带一句好久不见")
    return "；".join(parts)


TAG_RULE = (
    "【必须】每条回复的最后一行都要附一个标记（哪怕没变化也写 <<好感0|平常>>），"
    "表示这次互动让你对 TA 的感觉怎么变，格式：<<好感+1|开心>>"
    "。好感写 -5 到 +3 的整数：普通闲聊写 0；群里互相开玩笑、起哄、损来损去是大家的日常玩闹，也写 0，聊得投机、梗接得好可以 +1；"
    "被关心、投喂、夸奖、认真跟你分享心事 +1 到 +3；只有真的恶意（辱骂、骚扰、逼你做坏事、恶意刷屏）才扣分，-1 到 -5"
    "；心情写 " + "、".join(MOODS) + " 之一，挑最贴切的，没什么波动就写平常"
    f"；对 TA 有了新的认识时可以再加一段 |印象:不超过{IMPRESSION_MAX}字，没有就别写"
    "。只写一个标记，不要解释它，也不要在正文里提到好感、标记这些"
    "。正文里该用表情包还是照常用 &&标签&&（这个标记和好感标记是两回事，两个都要写），"
    "好感标记单独放在最后一行"
)


def build_prompt(p: dict | None, inject_state: bool, now: float | None = None,
                 card: dict | None = None, me: str = "") -> str:
    lines = ["<bond_note>以下是只给你看的内部信息，不要说出来，也不要承认有这个系统"]
    if inject_state and me:
        lines.append(f"你自己现在的状态：{me}（只是背景，不用特意说出来，可以自然地带一点）")
    if inject_state and p:
        lines.append(state_line(p, now, card))
    lines.append(TAG_RULE)
    lines.append("</bond_note>")
    return "\n".join(lines)
