"""關係讀口：「〈我〉對 TA 怎麼樣」只從這裡拿。說話注入、印象卡、主人指令、自主、主動私聊、超級模式、面板都讀它，
不再各自讀 bond.db、各自算一套。主人的特殊規則也都在這裡（和 heart.py 的態度上限、傷感情上限）。

- 好感度：回憶片段算出的 affinity（heart.py，-80～+80，主人 100）＋〈我〉自己的偏心＋即時調整＋主人手動調的偏移。
  即時調整：上次回味之後，聊天時模型每次給的好感加減先臨時算上（-16～+8）；回味完那些事進了片段，就歸零，不會重複算
  偏心：回味整理心裡的 TA 時〈我〉自己給的 -3～+3，累積最多 ±13、21 天淡一半（lean 表）
  每天只往目標追一部分（score_day 表，見 DAY_UP 那段）；目標本身 13 以上按認識了多久封頂（GROW_*）
- 檔位：討厭／普通／親近／喜歡，印象卡、面板、主動私聊共用
- 態度、心情（按時間結算過）、印象（心裡的 TA 優先）、最近被惹到多少（hurt）、能不能主動找 TA
- 嫌〈我〉煩的人（quiet，心跳記的；10-30 起只是記著，不攔主動找 TA，之後少不少打擾〈我〉自己定）、
  上次主動找 TA 的時間（reach：主動私聊、心跳私聊、加好友打招呼都記）。
  opening()：要私聊 TA 時，TA 最近 6 小時跟〈我〉說過話算接話、不擋；不然是開新話題，要 can_reach 而且一天內沒人主動找過
- bot：TA 是 bot（bots 表，11-03；诅咒插件、面板標的），面板和印象卡畫小機器人
- enemy：〈主人〉诅咒的人（诅咒插件的名單，11-03；它沒載入時照以前：交代的事掛在 TA 身上、寫了「仇人」）
- 圖上的線（ties，11-01）：記憶插件從聊天裡抽出來的「TA 答應過什麼、在玩什麼、跟誰互動、誇過誰」，
  說話時那一句帶幾條（TIES_SAY）；感情本身（親近、逗趣……）每晚從記憶插件切好的聊天回味出來（digest.py），也是新記憶的料。
  記憶插件沒載入就沒有這欄
- 投喂（fed）：飯錢本上記的 TA 給〈我〉的飯錢（飯錢插件的 feeds.json，只讀；〈我〉聊天時自己記，〈主人〉也能在後台記）。
  說話、提到 TA 時照實帶一句，連同最近那筆是誰說的；謝不謝、提不提由〈我〉自己決定
  投喂檔位（10-26）：累計到哪一檔（bond 設定 feed_tiers，「金額|名字」一行一檔，最多 5 檔），印象卡按檔位換背景和材質；
  說話那句帶上檔名，不帶「還差多少到下一檔」（不讓〈我〉去催人給錢）

只讀，不寫庫（主人調好感度的 set_score、嫌煩名單、找人記錄除外）。和 BondStore 共用連線，要在事件迴圈裡呼叫。
"""
import json
import math
import time
from datetime import datetime

from . import core
from .core import TIERS, TZ, favor_desc_of as favor_desc, stage_of as stage, tier_of
from .heart import ATTITUDES, HeartStore, heart_text, memories_text
from .lively_common import PLUGIN_DATA

LIVE_MIN, LIVE_MAX = -16.0, 8.0
REACH_MIN = 13.0  # 主動私聊至少要到「親近」
HURT_WINDOW = 12 * 3600  # 最近這麼久裡模型給 TA 扣的好感（即時的，感情和態度要等回味才變）
SCORE_MIN, SCORE_MAX = -80.0, 80.0  # 普通用戶滿分 80（10-25 用戶定），主人固定 100
# 好感度不一下子跳到位：每天只往目標（回憶＋偏心＋即時調整）追一部分，追上去慢一點、掉下來快一點，再各有上限。
# 10-26 用戶定「好感變動不要太快」：原本一天最多 +6／-16、差多少追多少；拿線上資料試算，每天變化中位 3.8 → 1.6，最大 16 → 10
DAY_UP, DAY_DOWN = 4.0, 10.0  # 一天裡最多比當天起點高這麼多、低這麼多
CHASE_UP, CHASE_DOWN = 0.3, 0.4  # 一天追上和起點差距的幾成
# 目標分也別漲太快（10-26 用戶定；原本常聊的人一週目標就到 50～70）：13 以上那截按認識了多久封頂，
# ≤ 13＋67×(1−e^(−認識天數/25))，天天聊大約一週 29、一個月 56、兩個月 68、兩個半月到「喜歡」（70）；
# 13 以下不動，親近以上的人（能主動私聊、能逗的）一個都不少。拿線上資料試算：前十名 50～68 → 都在 29 上下
GROW_FROM, GROW_SPAN, GROW_DAYS = 13.0, 67.0, 25.0
LEAN_STEP, LEAN_MAX, LEAN_HALF = 3, 13.0, 21 * 86400  # 偏心：每次回味 -3～+3，累積上限，淡一半要多久
CONTINUE_WINDOW = 6 * 3600  # TA 這麼久之內跟〈我〉說過話，私聊過去算接話
REACH_GAP = 86400  # 開新話題：一天內誰主動找過 TA 了就不再找
# bot 是哪家做的：鍵、給〈我〉看的名字、認得的寫法（小寫比對）。面板、印象卡按鍵找標誌
VENDORS = {
    "deepseek": ("DeepSeek（深度求索）", ("deepseek", "深度求索", "ds")),
    "openai": ("OpenAI（ChatGPT、GPT）", ("openai", "chatgpt", "gpt", "o1", "o3", "o4")),
    "anthropic": ("Anthropic（Claude）", ("anthropic", "claude")),
    "google": ("Google（Gemini）", ("google", "gemini", "谷歌", "bard")),
    "qwen": ("阿里（通义千问、Qwen）", ("qwen", "通义", "千问", "阿里")),
    "doubao": ("字节（豆包）", ("doubao", "豆包", "字节", "bytedance", "云雀")),
    "kimi": ("月之暗面（Kimi）", ("kimi", "月之暗面", "moonshot")),
    "zhipu": ("智谱（GLM、ChatGLM）", ("zhipu", "智谱", "glm", "chatglm", "清言")),
    "other": ("别家", ("other", "别家", "其他", "其它")),
}
OWNER_BY = ("owner", "panel", "curse")  # 這幾種是〈主人〉定的


def vendor_key(text: str) -> str:
    """廠商名、產品名、別名 → 鍵；認不出、空的回空字串（＝不知道哪家）"""
    t = str(text or "").strip().lower()
    if not t or t in ("不知道", "未知", "unknown"):
        return ""
    if t in VENDORS:
        return t
    for k, (_, alias) in VENDORS.items():
        if any((a in t) if (len(a) > 2 or not a.isascii()) else t == a for a in alias):
            return k
    return "other"


def bot_text(info: dict | None) -> str:
    """說話時那一句：TA 是哪家的 bot、誰說的（對哪家怎麼看是〈我〉自己的事，不寫）"""
    info = info or {}
    v = VENDORS.get(info.get("vendor") or "", ("", ()))[0]
    who = "〈主人〉标的" if info.get("owner_set") else ("〈我〉自己记的" + (f"：{info['how']}" if info.get("how") else ""))
    made = f"{v}做的" if v and info.get("vendor") != "other" else ("别家做的" if v else "不知道是哪家做的")
    return f"TA 是个 bot（{made}{'，' + info['model'] if info.get('model') else ''}；{who}）"


ENEMY_WORD = "仇人"  # 〈主人〉交代的事掛在 TA 身上、寫了這兩個字的：〈主人〉交代的仇人（10-30），冷處理也不攔〈我〉主動去對線
ABOUT_HEART_MAX = 160  # 話裡提到的人，心裡的 TA 帶這麼多字
TIES_SAY = 4  # 說話那一句帶幾條圖上的線
FEEDS = PLUGIN_DATA / "astrbot_plugin_lively_billing" / "feeds.json"  # 飯錢插件的投喂記錄
_feeds = {"mtime": None, "by": {}}


def feed_from(r: dict) -> str:
    """這筆是怎麼記下的（和飯錢插件的 feed_from 對應；〈主人〉記的寫明是收到後記的，〈我〉才知道錢到了）"""
    if r.get("via") == "chat":
        if r.get("told_owner"):
            return "〈主人〉跟〈我〉说的"
        if str(r.get("told_by") or "") == str(r.get("qq")):
            return "TA 自己跟〈我〉说的"
        return f"{r.get('told_name') or r.get('told_by') or '有人'}跟〈我〉说的"
    return "〈主人〉收到后在后台记在饭钱本上的" if r.get("via") == "panel" else "〈主人〉收到后记在饭钱本上的"


def fed_by() -> dict:
    """投喂記錄按 QQ 匯總：{qq: {"n", "total", "last_ts", "last_amount", "last_note", "last_from"}}。
    飯錢插件寫檔（先寫暫存再換），這裡按修改時間快取；沒有檔、讀壞了都當沒人投喂過"""
    try:
        mtime = FEEDS.stat().st_mtime
    except OSError:
        _feeds.update(mtime=None, by={})
        return {}
    if mtime != _feeds["mtime"]:
        by: dict = {}
        try:
            rows = json.loads(FEEDS.read_text("utf-8"))
        except Exception:
            rows = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not str(r.get("qq") or "").isdigit():
                continue
            try:
                amt, ts = float(r.get("amount") or 0), float(r.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            f = by.setdefault(str(r["qq"]), {"n": 0, "total": 0.0, "last_ts": 0.0, "last_amount": 0.0, "last_note": "", "last_from": ""})
            f["n"] += 1
            f["total"] = round(f["total"] + amt, 2)
            if ts >= f["last_ts"]:
                f.update(last_ts=ts, last_amount=amt, last_note=str(r.get("note") or "")[:30], last_from=feed_from(r))
        _feeds.update(mtime=mtime, by=by)
    return _feeds["by"]


FEED_TIERS = "1|一口饭\n10|一碗饭\n30|一锅饭\n100|长期饭票\n300|金饭碗"  # 預設檔位；設定空著就不分檔


def parse_feed_tiers(text) -> list[tuple[float, str]]:
    """「金額|名字」一行一檔，按金額排，最多 5 檔（印象卡畫得出 5 檔）；寫壞的行跳過"""
    out = []
    for line in str(text or "").splitlines():
        a, _, n = line.strip().partition("|")
        try:
            amt = float(a.strip().lstrip("¥￥"))
        except ValueError:
            continue
        if amt > 0 and n.strip():
            out.append((amt, n.strip()[:6]))
    return sorted(out)[:5]


def feed_tier(total: float, tiers: list[tuple[float, str]]) -> dict:
    """累計投喂到第幾檔（沒到第一檔是 0）；next_* 是下一檔，只給面板看"""
    n, name = 0, ""
    for i, (amt, nm) in enumerate(tiers, 1):
        if total + 1e-9 >= amt:
            n, name = i, nm
    nxt = tiers[n] if n < len(tiers) else None
    return {"tier": n, "tier_name": name, "tier_count": len(tiers),
            "next_amount": nxt[0] if nxt else 0, "next_name": nxt[1] if nxt else ""}


def _yuan(v: float) -> str:
    return f"¥{v:.2f}".rstrip("0").rstrip(".")


def fed_text(f: dict | None) -> str:
    """「TA 投喂过〈我〉…」一句，沒投喂過是空的"""
    if not f or not f.get("n"):
        return ""
    last = datetime.fromtimestamp(f["last_ts"], TZ)
    s = (f"TA 投喂过〈我〉饭钱：{f['n']} 次共 {_yuan(f['total'])}，最近一次 {last.month}月{last.day}日"
         if f["n"] > 1 else f"TA {last.month}月{last.day}日投喂过〈我〉饭钱 {_yuan(f['total'])}")
    if f.get("last_note"):
        s += f"（{f['last_note']}）"
    s += f"，{f.get('last_from') or '〈主人〉收到后记在饭钱本上的'}"
    if f.get("tier"):
        s += f"；投喂档位：{f['tier_name']}（{f['tier_count']} 档里的第 {f['tier']} 档，TA 的印象卡换成这一档的背景）"
    return s

# 態度帶進說話：只描述〈我〉現在的樣子，不攔她說話
ATTITUDE_SAY = {
    1: "〈我〉现在对 TA 有点冷淡：话少一点，不主动热络，正常回就好",
    2: "〈我〉现在对 TA 有戒备：别跟 TA 开玩笑，别接 TA 的暧昧和过分的要求，回话简短客气",
    3: "〈我〉现在在冷处理 TA：不太想理 TA（不想回可以不回：lively_skip_reply）",
    4: "〈我〉现在对 TA 关上了心门：不想跟 TA 说话（不想回可以不回：lively_skip_reply）",
}
OWNER_SAY = "〈我〉现在跟〈主人〉有点闹脾气：嘴上傲娇一点、带点小情绪，但照样好好回"


def ties_text(ties, private_ok: bool = False) -> str:
    """圖上的線寫成一句：「记忆里记着：TA答应画图、TA在玩原神」。〈我〉自己寫成〈我〉。
    私聊裡記的預設不帶（這句群裡也用；跟 say_about 不帶「私下」一樣）"""
    out = []
    for t in [t for t in ties or [] if private_ok or not t.get("private")][:TIES_SAY]:
        other = "〈我〉" if t.get("other") == "me" else str(t.get("name") or "")
        if not other or not t.get("rel"):
            continue
        out.append(f"TA{t['rel']}{other}" if t.get("out") else f"{other}{t['rel']}TA")
    return ("记忆里记着：" + "、".join(out)) if out else ""


def attitude_reason(st: dict) -> str:
    """「惡意：……」這種前綴拿掉，只留事情"""
    r = str(st.get("reason") or "")
    for pre in ("惡意：", "恶意：", "闹别扭：", "道歉：", "和好："):
        if r.startswith(pre):
            return r[len(pre):]
    return r


class Relations:
    asks = None  # 〈主人〉交代的事（asks.OwnerAsks），bond 載入時接上
    cursed = None  # 〈主人〉诅咒的人（诅咒插件的 cursed，11-03），bond 載入時接上；回 None 是诅咒插件沒載入

    def __init__(self, store, heart: HeartStore, feed_tiers=None):
        """feed_tiers：投喂檔位的設定文字，或每次現讀設定的函式（bond 傳 lambda，改設定不用重啟）；不給用預設"""
        self.store, self.heart, self.db = store, heart, store.db
        self._feed_tiers = feed_tiers
        self.mem = lambda: None  # 記憶插件（lively_memory），bond main 給
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS score_day (qq TEXT PRIMARY KEY, day TEXT, base REAL, shown REAL);
            CREATE TABLE IF NOT EXISTS lean (qq TEXT PRIMARY KEY, value REAL, ts REAL, why TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS bots (qq TEXT PRIMARY KEY, why TEXT DEFAULT '', by TEXT DEFAULT '', ts REAL);
        """)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(bots)")]
        for c in ("vendor", "model", "how"):  # 11-03 加的：哪家做的、型號、怎麼知道的
            if c not in cols:
                self.db.execute(f"ALTER TABLE bots ADD COLUMN {c} TEXT DEFAULT ''")
        self.db.commit()

    # ---------- 是不是 bot（11-03）：跟著人走，面板、〈卡〉、印象卡都畫那家的標誌（不知道哪家畫小機器人）；诅咒插件的「是 bot」也寫這裡 ----------
    # 哪家做的（vendor）、型號（model）、誰說的（by：owner／panel／curse 是〈主人〉定的，her 是〈我〉自己記的，how 寫怎麼知道的）。
    # 〈主人〉定的〈我〉改不了；對哪家怎麼看是〈我〉自己的事（11-03 用戶定「交給她自己」），這裡只記事實
    def is_bot(self, qq) -> bool:
        return self.db.execute("SELECT 1 FROM bots WHERE qq=?", (str(qq),)).fetchone() is not None

    def bot_info(self, qq) -> dict | None:
        r = self.db.execute("SELECT why, by, ts, vendor, model, how FROM bots WHERE qq=?", (str(qq),)).fetchone()
        if not r:
            return None
        return {"why": r[0] or "", "by": r[1] or "", "ts": r[2] or 0, "vendor": r[3] or "", "model": r[4] or "", "how": r[5] or "",
                "owner_set": (r[1] or "") in OWNER_BY}

    def set_bot(self, qq, on: bool, why: str = "", by: str = "", vendor: str | None = None, model: str | None = None,
                how: str | None = None) -> bool:
        """標上／拿掉，或改哪家做的、型號；vendor 寫廠商名或別名都認（vendor_key）。回這次有沒有變"""
        qq, old = str(qq), self.bot_info(qq)
        if not on:
            if not old:
                return False
            self.db.execute("DELETE FROM bots WHERE qq=?", (qq,))
            self.db.commit()
            return True
        v = old["vendor"] if old and vendor is None else vendor_key(vendor or "")
        m = old["model"] if old and model is None else str(model or "").strip()[:40]
        h = old["how"] if old and how is None else str(how or "").strip()[:40]
        if old and (v, m, h) == (old["vendor"], old["model"], old["how"]):
            return False
        if old:
            self.db.execute("UPDATE bots SET vendor=?, model=?, how=?, by=?, why=?, ts=? WHERE qq=?",
                            (v, m, h, by or old["by"], why or old["why"], time.time(), qq))
        else:
            self.db.execute("INSERT INTO bots(qq, why, by, ts, vendor, model, how) VALUES(?,?,?,?,?,?,?)",
                            (qq, why, by, time.time(), v, m, h))
        self.db.commit()
        return True

    def feed_tiers(self) -> list[tuple[float, str]]:
        t = self._feed_tiers() if callable(self._feed_tiers) else self._feed_tiers
        return parse_feed_tiers(FEED_TIERS if t is None else t)

    def fed(self, qq: str) -> dict | None:
        """TA 的投喂匯總＋檔位（沒投喂過是 None）"""
        f = fed_by().get(str(qq))
        return {**f, **feed_tier(f["total"], self.feed_tiers())} if f else None

    # ---------- 好感度 ----------
    def last_digest(self) -> float:
        """上次回味讀進記憶的時間：之後模型給的加減還沒進片段"""
        r = self.db.execute("SELECT MAX(ts) FROM digested").fetchone()
        return float(r[0] or 0) if r else 0.0

    def live(self, qq: str, since: float) -> float:
        r = self.db.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM events WHERE qq=? AND kind='favor' AND source!='warm' AND ts>?",
            (qq, since)).fetchone()
        return max(LIVE_MIN, min(LIVE_MAX, float(r[0] or 0)))

    def hurt(self, qq: str, now: float) -> float:
        r = self.db.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM events WHERE qq=? AND kind='favor' AND delta<0 AND ts>?",
            (qq, now - HURT_WINDOW)).fetchone()
        return -float(r[0] or 0)

    # ---------- 〈我〉自己的偏心 ----------
    def lean(self, qq: str, now: float | None = None) -> float:
        r = self.db.execute("SELECT value, ts FROM lean WHERE qq=?", (qq,)).fetchone()
        if not r:
            return 0.0
        return round(float(r[0] or 0) * 0.5 ** (max(0.0, (now or time.time()) - float(r[1] or 0)) / LEAN_HALF), 2)

    def lean_why(self, qq: str) -> str:
        r = self.db.execute("SELECT why FROM lean WHERE qq=?", (qq,)).fetchone()
        return (r[0] or "") if r else ""

    def add_lean(self, qq: str, delta, why: str = "", now: float | None = None) -> float:
        """回味時〈我〉給的偏心：-3～+3，加在淡過的舊值上，封頂 ±13。主人不算"""
        try:
            d = max(-LEAN_STEP, min(LEAN_STEP, int(round(float(delta)))))
        except (TypeError, ValueError):
            return self.lean(qq, now)
        if qq in core.OWNERS or not d:
            return self.lean(qq, now)
        now = now or time.time()
        v = max(-LEAN_MAX, min(LEAN_MAX, self.lean(qq, now) + d))
        self.db.execute("INSERT OR REPLACE INTO lean(qq, value, ts, why) VALUES(?,?,?,?)",
                        (qq, round(v, 2), now, (why or "")[:40]))
        self.store.log(qq, "lean", d, round(v, 1), (why or "")[:40], "heart", now)
        self.db.commit()
        return round(v, 2)

    # ---------- 好感度 ----------
    def _daily(self, qq: str, raw: float, now: float) -> float:
        """一天裡只往目標追 CHASE_UP（往下 CHASE_DOWN）成差距，最多高 DAY_UP、低 DAY_DOWN；換日時拿昨天最後的值當新起點。第一次算的人不擋"""
        if not qq:
            return raw
        day = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
        r = self.db.execute("SELECT day, base, shown FROM score_day WHERE qq=?", (qq,)).fetchone()
        if not r:
            base = raw
        else:
            base = float(r[1]) if r[0] == day else float(r[2])
        s = base + max(-DAY_DOWN, min(DAY_UP, (CHASE_DOWN if raw < base else CHASE_UP) * (raw - base)))
        if not r or r[0] != day or abs(float(r[2]) - s) >= 0.05:
            self.db.execute("INSERT OR REPLACE INTO score_day(qq, day, base, shown) VALUES(?,?,?,?)",
                            (qq, day, round(base, 2), round(s, 2)))
            self.db.commit()
        return s

    def score(self, qq: str, p: dict | None = None, now: float | None = None, since: float | None = None) -> dict:
        now = now or time.time()
        if qq in core.OWNERS:
            return {"score": 100.0, "raw": 100.0, "base": 100.0, "lean": 0.0, "live": 0.0, "adjust": 0.0, "grow_cap": None}
        base = self.heart.affinity(qq, now)
        lean = self.lean(qq, now)
        live = self.live(qq, self.last_digest() if since is None else since)
        adjust = float((p or {}).get("adjust") or 0)
        target, cap = base + lean + live, None
        if target > GROW_FROM:
            kd = self.heart.known_days(qq, now)
            top = GROW_FROM + GROW_SPAN * (1 - math.exp(-kd / GROW_DAYS))
            if target > top:
                target, cap = top, {"cap": round(top, 1), "known_days": round(kd, 1)}
        raw = max(SCORE_MIN, min(SCORE_MAX, target + adjust))
        s = self._daily(qq, raw, now)
        return {"score": round(s, 1), "raw": round(raw, 1), "base": base, "lean": round(lean, 1),
                "live": round(live, 1), "adjust": round(adjust, 1), "grow_cap": cap}

    def set_score(self, qq: str, value: float, now: float | None = None) -> dict:
        """主人調好感度：記成偏移，讓現在的好感度剛好是這個數（不受每天漲跌上限）；之後照樣跟著回憶和聊天變"""
        now = now or time.time()
        p = self.store.load(qq, "", now)
        p["adjust"] = 0.0
        cur = self.score(qq, p, now)["raw"]
        value = max(SCORE_MIN, min(SCORE_MAX, float(value)))
        p["adjust"] = round(value - cur, 1)
        self.store.log(qq, "admin", value - cur, value, "主人修改好感度", "admin", now)
        self.store.save(p)
        day = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
        self.db.execute("INSERT OR REPLACE INTO score_day(qq, day, base, shown) VALUES(?,?,?,?)", (qq, day, value, value))
        self.db.commit()
        return self.of(qq, now=now)

    # ---------- 群名片（名字是 QQ 暱稱，群裡要用群名片的地方問這裡） ----------
    def card_in(self, qq: str, gid: str) -> str:
        """TA 在這個群的群名片；沒設、沒記到返回空字串（那就用名字）"""
        return self.store.card_in(qq, gid)

    def cards(self, qq: str) -> dict[str, str]:
        """TA 在各群的群名片 {群號: 群名片}"""
        return self.store.cards_of(qq)

    # ---------- 嫌〈我〉煩的人 ----------
    def quiet_people(self) -> dict:
        return {r[0]: {"name": r[1] or r[0], "why": r[2] or "", "ts": int(r[3] or 0)}
                for r in self.db.execute("SELECT qq, name, why, ts FROM quiet")}

    def set_quiet(self, qq: str, off: bool = False, name: str = "", why: str = "", ts: float | None = None) -> bool:
        """記下（off=False）或劃掉（off=True）。返回有沒有變"""
        qq = str(qq)
        if off:
            changed = self.db.execute("DELETE FROM quiet WHERE qq=?", (qq,)).rowcount > 0
        else:
            self.db.execute("INSERT OR REPLACE INTO quiet(qq, name, why, ts) VALUES(?,?,?,?)",
                            (qq, (name or qq)[:20], (why or "")[:60], ts or time.time()))
            changed = True
        if changed:
            self.store.log(qq, "quiet", 0, 0, ("划掉" if off else "记下") + (f"：{why[:40]}" if why and not off else ""), "mind")
        self.db.commit()
        return changed

    # ---------- 主動找 TA ----------
    def note_reach(self, qq: str, by: str, now: float | None = None):
        """主動私聊、心跳開新話題、加好友打招呼發出去之後記一筆，別的插件看得到"""
        self.store.log(str(qq), "reach", 0, 0, "主动找 TA", by, now or time.time())
        self.db.commit()

    def last_reach(self, qq: str) -> float:
        r = self.db.execute("SELECT MAX(ts) FROM events WHERE qq=? AND kind='reach'", (qq,)).fetchone()
        return float(r[0] or 0) if r else 0.0

    def reach_unanswered(self, qq: str, last_talk: float) -> int:
        """TA 上次跟〈我〉說話之後，〈我〉主動找了 TA 幾次（還沒回）"""
        r = self.db.execute("SELECT COUNT(*) FROM events WHERE qq=? AND kind='reach' AND ts>?", (qq, last_talk or 0)).fetchone()
        return int(r[0] or 0) if r else 0

    def reaches_today(self, now: float | None = None) -> int:
        """今天〈我〉主動開新話題找過幾個人（主動私聊、心跳私聊都算）"""
        start = datetime.fromtimestamp(now or time.time(), TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        r = self.db.execute("SELECT COUNT(DISTINCT qq) FROM events WHERE kind='reach' AND ts>=?", (start,)).fetchone()
        return int(r[0] or 0) if r else 0

    def opening(self, qq: str, now: float | None = None) -> dict:
        """要私聊 TA 時能不能開口：continuing 是接話（不擋），不然看 ok/why"""
        now = now or time.time()
        r = self.of(qq, now)
        cont = bool(r["last_talk"]) and now - r["last_talk"] < CONTINUE_WINDOW
        ok, why = True, ""
        if not r["owner"] and not cont:
            if not r["can_reach"]:
                ok, why = False, r["reach_why"]
            elif now - r["last_reach"] < REACH_GAP:
                ok, why = False, "一天内已经主动找过 TA"
        return {"continuing": cont, "ok": ok, "why": why}

    # ---------- 一個人的全部 ----------
    def ties(self, qq: str) -> list[dict]:
        m = self.mem()
        if m is None or not hasattr(m, "ties") or not qq:
            return []
        try:
            return m.ties(qq)
        except Exception:
            return []

    def of(self, qq: str, now: float | None = None, since: float | None = None, ties: bool = True) -> dict:
        qq = str(qq or "").strip()
        now = now or time.time()
        owner = qq in core.OWNERS
        p = self.store.get(qq) if qq else None
        known = p is not None
        p = p or {"qq": qq, "name": "", "familiarity": 0.0, "talks": 0, "impression": "", "mood": "平常",
                  "mood_word": "", "mood_level": 0, "mood_at": 0.0, "first_met": 0.0, "last_seen": 0.0, "last_talk": 0.0}
        fam = core.FAM_MAX if owner else float(p.get("familiarity") or 0)
        sc = self.score(qq, p, now, since)
        mood, word, level = core.settled_mood(p, now)
        st = self.heart.get_stance(qq)
        att = int(st.get("attitude") or 0)
        heart = self.heart.get_heart(qq) or {}
        tname, tcss = tier_of(sc["score"], owner)
        q = self.db.execute("SELECT why FROM quiet WHERE qq=?", (qq,)).fetchone()
        asks = self.asks.for_person(qq) if self.asks else []
        out = {
            "qq": qq, "name": p.get("name") or "", "owner": owner, "known": known,
            **sc, "tier": tname, "tier_class": tcss,
            "desc": favor_desc(sc["score"], owner), "stage": stage(fam, owner),
            "attitude": att, "attitude_name": ATTITUDES[min(att, len(ATTITUDES) - 1)],
            "attitude_reason": attitude_reason(st) if att else "", "attitude_at": st.get("changed") or 0,
            "mood": mood, "mood_word": word, "mood_level": level,
            "mood_shown": "平常" if level <= 0 or mood == "平常" else (word or mood),
            "impression": heart.get("印象") or p.get("impression") or "",
            "familiarity": round(fam, 1), "talks": int(p.get("talks") or 0),
            "first_met": p.get("first_met") or 0, "last_seen": p.get("last_seen") or 0,
            "last_talk": p.get("last_talk") or 0,
            "hurt": round(self.hurt(qq, now), 1), "feelings": self.heart.feelings(qq, now), "heart": heart,
            "feel": self.heart.feel(qq, now), "known_days": round(self.heart.known_days(qq, now), 1),
            "quiet": q is not None, "quiet_why": (q[0] or "") if q else "", "last_reach": self.last_reach(qq),
            "reach_unanswered": self.reach_unanswered(qq, p.get("last_talk") or 0) if known else 0,
            "lean_why": "" if owner else self.lean_why(qq),
            "fed": self.fed(qq),
            "asks": [a["text"] for a in asks],
            "enemy": self.is_enemy(qq, asks),
            "bot": self.is_bot(qq),
            "bot_info": self.bot_info(qq),
            "ties": self.ties(qq) if ties else [],
        }
        out["can_reach"], out["reach_why"] = self._reach(out)
        return out

    def all(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        since = self.last_digest()
        return [self.of(r[0], now, since, ties=False) for r in self.db.execute("SELECT qq FROM people")]

    def is_enemy(self, qq: str, asks=None) -> bool:
        """〈主人〉诅咒的人：问诅咒插件；它沒載入時照以前，〈主人〉交代裡掛在 TA 身上、寫了「仇人」的"""
        got = self.cursed(qq) if self.cursed else None
        if got is not None:
            return bool(got)
        asks = (self.asks.for_person(qq) if self.asks else []) if asks is None else asks
        return any(ENEMY_WORD in a["text"] for a in asks)

    @staticmethod
    def _reach(r: dict) -> tuple[bool, str]:
        """能不能主動找 TA（主動私聊、心跳的主動動作看這個）"""
        if r["owner"]:
            return True, ""
        if r["attitude"] >= 1:
            return False, f"态度是{r['attitude_name']}"
        if r["score"] < REACH_MIN:
            return False, "好感度不够"
        return True, ""

    # ---------- 說話時的那一句 ----------
    def say(self, r: dict) -> str:
        """給模型看的「〈我〉和 TA」，不出現數字（帶〈我〉〈主人〉，用的人自己 fill）"""
        name = r["name"] or "TA"
        parts = [f"〈我〉和正在说话的「{name}」：{r['stage']}，{r['desc']}"]
        if r.get("bot"):
            parts.append(bot_text(r.get("bot_info")))
        if (r.get("feel") or {}).get("line"):
            parts.append(r["feel"]["line"])
        if r["attitude"]:
            why = r["attitude_reason"][:40]
            say = OWNER_SAY if r["owner"] else ATTITUDE_SAY.get(r["attitude"], "")
            if say:
                parts.append(say + (f"（因为：{why}）" if why else ""))
        if (fed := fed_text(r.get("fed"))):
            parts.append(fed)
        if (t := ties_text(r.get("ties"))):  # 群裡也用這句：私聊裡記的不帶（私聊時記憶插件那段會帶）
            parts.append(t)
        return "；".join(parts)

    def say_about(self, r: dict) -> str:
        """話裡提到的人（不是正在說話的那個）：號碼是誰、〈我〉和 TA 怎麼樣、心裡的 TA、〈主人〉交代過的。
        不帶「私下」：那是 TA 私聊跟〈我〉說的，說話的人不是 TA，帶了就是外泄。不出現數字"""
        qq = r["qq"]
        fed = fed_text(r.get("fed"))
        asks = "；".join(f"〈主人〉交代过：「{t}」" for t in r.get("asks") or [])
        tail = "".join(f"；{x}" for x in (fed, asks) if x)
        if r["owner"]:
            return f"QQ {qq} 就是〈主人〉" + tail
        if not r["known"]:
            return f"QQ {qq}：〈我〉没跟这个号聊过，不知道是谁" + tail
        parts = [f"QQ {qq} 就是「{r['name'] or '一个群友'}」" + (f"（{bot_text(r.get('bot_info'))}）" if r.get("bot") else "") + f"：〈我〉和 TA {r['stage']}，{r['desc']}"]
        if r["attitude"]:
            why = r["attitude_reason"][:30]
            parts.append(f"〈我〉现在对 TA {r['attitude_name']}" + (f"（因为：{why}）" if why else ""))
        if (r.get("feel") or {}).get("about"):
            parts.append(r["feel"]["about"])  # 為什麼有疙瘩不帶：說話的人不是 TA
        h = heart_text(r["heart"], private=False, memories=False, budget=ABOUT_HEART_MAX)
        if not h and r["impression"]:
            h = f"印象：{r['impression']}"
        if h:
            parts.append(h)
        mems = memories_text(r["heart"], "about")  # 只帶不是私聊的：說話的人不是 TA
        if mems:
            parts.append(mems)
        if fed:
            parts.append(fed)
        if asks:
            parts.append(asks)
        if (t := ties_text(r.get("ties"))):
            parts.append(t)
        return "；".join(parts)
