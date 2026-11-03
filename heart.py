"""情感的記憶層：記憶片段、心裡的 TA、態度。

情感從記憶來：和某個人有關的每一件事（片段）都帶著〈我〉當時的感受，和它對各種感情的影響；
對 TA 的長期感情 = 這些片段隨時間淡去之後的加總，所以「為什麼這樣看 TA」能追到具體是哪幾件事。

- 片段：每晚從 LivingMemory 的記憶裡拆出來（見 digest.py），一件事牽涉幾個人就給每個人各記一條（各自的感受不同）
- 心裡的 TA：稱呼、一句印象（留著以前的版本）、喜好、雷區、我們的梗、說話語氣、牽掛；私聊裡知道的事放在「私下」，只在私聊用；
  還有專屬回憶時刻（「回忆」，見 MEMORY_MAX 上面的說明）
- 態度：0 正常、1 冷淡、2 戒備、3 冷處理、4 封閉。由片段裡的惡意和誠懇道歉推動，規則見 apply_stance()

純邏輯 + SQLite，和 core.py 共用 bond.db，不依賴 AstrBot。
"""
import json
import math
import re
import time
from datetime import datetime

from . import core
from .core import DAY, TZ

# 此刻的情緒（片段的「感受」也從這裡挑）
FEELINGS = ("开心", "得意", "感动", "害羞", "期待", "好奇", "怀念", "释然", "吃醋", "委屈", "心疼", "担心",
            "无奈", "嫌弃", "失望", "小生气", "警惕", "不屑", "愧疚", "平常")
FEELING_ALIASES = dict(core.MOOD_ALIASES, 难受="委屈", 舍不得="怀念", 放心="释然", 后悔="愧疚", 不好意思="害羞",
                       戒备="警惕", 讨厌="嫌弃", 鄙夷="不屑", 好奇心="好奇", 盼着="期待", 心疼="心疼", 伤心="委屈")

# 長期感情：每個維度各自的半衰期（天）。芥蒂來得快去得快，反感留得久
# 10-27 從八種收成六種：信任、依賴對群友幾乎沒用上（1506 段片段裡 32、3 次），併進亲近。
# 以前的片段照樣帶著信任、依賴，讀的時候換成亲近（DIM_ALIASES），資料庫不改
DIMS = ("亲近", "逗趣", "牵挂", "欣赏", "芥蒂", "反感")
DIM_ALIASES = {"信任": "亲近", "依赖": "亲近"}
HALF_LIFE = {"亲近": 45, "逗趣": 30, "牵挂": 10, "欣赏": 60, "芥蒂": 7, "反感": 45}
# 芥蒂、反感的消解（道歉、和好）記成負的影響：這兩個維度不按重要程度延長半衰期，免得道歉比冒犯先淡掉、反感又冒回來
FIXED_DECAY = {"芥蒂", "反感"}
IMPACT_MAX = {d: (3 if d in FIXED_DECAY else 2) for d in DIMS}
# 同一個人、同一種感情，一天最多算 2 分（加、減各自算）：聊得密的人不會幾天就把感情頂滿。
# 但當天有一件單獨就很重的事（芥蒂、反感 3），那天照它的分量算，嚴重的惡意要狠狠記下，零碎的小委屈不會跟著漲；
# 主人常要試功能、試邊界，傷感情的那一邊一天最多 1，長得最慢
DAY_CAP = 2.0
AFFINITY_SCALE = 0.8  # 傾向 -100～100 × 把握 × 這個＝好感度（普通用戶 -80～+80）
OWNER_HURT_CAP = 1.0
KNOWN_HALF = 60.0  # 認識多久（known_days）：一天的片段過這麼多天算一半
SATURATE = 10.0  # 加總後的分數換成 0～100：大約 10 分淡去後的影響到六成多，23 分到九成
# 天平：壞事比好事重，一件芥蒂抵兩件好事，一件反感抵四件（第二階段的綜合分也從這裡來）
BALANCE_W = {"芥蒂": 2.0, "反感": 4.0}

KINDS = ("日常", "玩笑", "梗", "心事", "关心", "帮忙", "夸奖", "承诺", "冲突", "恶意", "道歉", "和好")

MOMENT_TEXT_MAX = 40

# 態度
ATTITUDES = ("正常", "冷淡", "戒备", "冷处理", "封闭")
OWNER_ATTITUDE_MAX = 1  # 主人最多鬧脾氣到冷淡
# 冷處理以上要有確實的惡意：一次不太重要的（重要 1～2）最多到戒備，再犯或很嚴重的才會更高
SEVERE_WEIGHT = 0.6
MINOR_CAP = 2
OFFENSE_GAP = 6 * 3600  # 6 小時內的惡意算同一次
APOLOGY_GAP = 6 * 3600  # 同一次惡意之後，6 小時內的道歉算同一次（記憶常有重疊，同一句道歉會被拆成好幾段片段）
# 冷淡、戒備沒再犯就慢慢緩和：第幾次犯 -> 要多少天；冷處理以上只能靠誠懇道歉往下降
EASE_DAYS = {1: 3, 2: 10}
EASE_DAYS_MORE = 30
EASE_COLD_DAYS = 1  # 冷淡（1 級）一天沒再犯就回正常（犯滿三次的除外）
FLOOR_AFTER = 3  # 犯滿這麼多次，自己最多只能緩和到冷淡，再往下要主人出手

HEART_IMPRESSION_MAX = 30
# 專屬回憶時刻（10-27 用戶定「每个用户都有专属回忆时刻，具体哪些算要让小肥鱼决定」）：〈我〉和 TA 之間只屬於你們倆、
# 〈我〉以後還會想起來的事。起因 10-27 13:29 泠·月在群裡說「你说的嘛，只让我摸」，〈我〉前一天在私聊說過「就你一个能摸，别人不许」，
# 群裡想不起來、說人家編的。哪些算由〈我〉定：每晚整理心裡的 TA 時挑（digest.HEART_RULE），聊天時也能自己加、刪（lively_heart_edit）。
# 每條 {"事", "哪天", "私聊"}。TA 跟〈我〉說話時整欄都帶上（不跟著心裡的 TA 截斷）；私聊裡的在群裡也帶，標明群裡別人不知道，
# 提不提由〈我〉判斷（10-23 定的：私聊的平常事群裡要接得住）。話裡提到的別人只帶不是私聊的
MEMORY_MAX = 6
MEMORY_WIDTH = 30
HEART_HISTORY = 8


def norm_feeling(word: str) -> str:
    word = (word or "").strip()
    if word in FEELINGS:
        return word
    return FEELING_ALIASES.get(word) or "平常"


def _clamp_impacts(raw) -> dict:
    """整理成 {維度: 整數}：舊名字（信任、依賴）換成現在的，同一件事落到同一個維度的加起來再限幅"""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        k = str(k).strip()
        k = DIM_ALIASES.get(k, k)
        if k not in DIMS:
            continue
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            continue
        out[k] = max(-IMPACT_MAX[k], min(IMPACT_MAX[k], out.get(k, 0) + v))
    return {k: v for k, v in out.items() if v}


def _fmt_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d")


class HeartStore:
    """和 BondStore 共用同一個連線"""

    def __init__(self, db):
        self.db = db
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS moments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq TEXT, ts REAL, scope TEXT DEFAULT '', private INTEGER DEFAULT 0,
                text TEXT, feeling TEXT DEFAULT '平常', kind TEXT DEFAULT '日常', weight REAL DEFAULT 0.4,
                impacts TEXT DEFAULT '{}', others TEXT DEFAULT '[]',
                source TEXT DEFAULT '', ref TEXT,
                recalls INTEGER DEFAULT 0, last_recall REAL DEFAULT 0, created REAL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_moments_qq ON moments(qq, ts);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_moments_ref ON moments(ref, qq);
            CREATE TABLE IF NOT EXISTS hearts (qq TEXT PRIMARY KEY, data TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS stance (
                qq TEXT PRIMARY KEY, attitude INTEGER DEFAULT 0, offenses INTEGER DEFAULT 0,
                last_offense REAL DEFAULT 0, apologies INTEGER DEFAULT 0, last_apology REAL DEFAULT 0,
                changed REAL DEFAULT 0, reason TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS digested (doc_id TEXT PRIMARY KEY, ts REAL, n INTEGER DEFAULT 0);
            """
        )
        self.db.commit()

    # ---------- 片段 ----------
    def add_moment(self, qq: str, ts: float, text: str, *, feeling="平常", kind="日常", weight=0.4,
                   impacts=None, others=None, scope="", private=False, source="", ref=None) -> int | None:
        text = (text or "").strip()[:MOMENT_TEXT_MAX]
        if not qq or not text:
            return None
        kind = kind if kind in KINDS else "日常"
        cur = self.db.execute(
            "INSERT OR IGNORE INTO moments(qq,ts,scope,private,text,feeling,kind,weight,impacts,others,source,ref,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (qq, ts, scope, int(bool(private)), text, norm_feeling(feeling), kind, max(0.1, min(1.0, float(weight))),
             json.dumps(_clamp_impacts(impacts), ensure_ascii=False),
             json.dumps([str(x) for x in (others or []) if str(x) != qq][:8]), source, ref, time.time()))
        return cur.lastrowid if cur.rowcount else None

    def moments(self, qq: str, limit: int = 10, since: float = 0, public_only: bool = False,
                created_since: float = 0) -> list[dict]:
        sql = "SELECT * FROM moments WHERE qq=? AND ts>? AND created>?" + (" AND private=0" if public_only else "")
        rows = self.db.execute(sql + " ORDER BY ts DESC LIMIT ?", (qq, since, created_since, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["impacts"] = _clamp_impacts(json.loads(d["impacts"] or "{}"))
            d["others"] = json.loads(d["others"] or "[]")
            out.append(d)
        return out

    # ---------- 當場記的（「记住」，10-28）----------
    def live_today(self, qq: str, now: float | None = None) -> int:
        """今天當場記了 TA 幾條（一天的上限見 main.REMEMBER_DAILY）"""
        now = now or time.time()
        day0 = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return self.db.execute("SELECT COUNT(*) FROM moments WHERE qq=? AND source='live' AND created>=?",
                               (qq, day0)).fetchone()[0]

    def fresh(self, qq: str, now: float | None = None, hours: float = 24, n: int = 3, public_only: bool = False) -> list[dict]:
        """這一天當場記下的、和 TA 的事（新的在前）：說話時擺在〈我〉手邊，不用等凌晨回味"""
        now = now or time.time()
        sql = "SELECT * FROM moments WHERE qq=? AND source='live' AND ts>?" + (" AND private=0" if public_only else "")
        return [dict(r) for r in self.db.execute(sql + " ORDER BY ts DESC LIMIT ?", (qq, now - hours * 3600, n))]

    def live_on_day(self, qqs, ts: float) -> dict[str, list[str]]:
        """回味用：這些人在 ts 那天當場記過的事（同一件事回味時不用再拆一條）"""
        day0 = datetime.fromtimestamp(ts, TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        out = {}
        for q in qqs:
            rows = self.db.execute("SELECT text FROM moments WHERE qq=? AND source='live' AND ts>=? AND ts<?",
                                   (q, day0, day0 + DAY)).fetchall()
            if rows:
                out[q] = [r[0] for r in rows]
        return out

    def moment_count(self, qq: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM moments WHERE qq=?", (qq,)).fetchone()[0]

    def moments_by_ref(self, ref: str) -> list[dict]:
        """從某一條長期記憶（ref＝LivingMemory 的 doc_id）回味出的片段"""
        return [dict(r) for r in self.db.execute("SELECT * FROM moments WHERE ref=?", (str(ref),))]

    def forget_ref(self, ref: str) -> list[dict]:
        """那條長期記憶忘掉了：回味出的片段一起刪，回傳刪掉的（給呼叫的人備份）。
        digested 那行留著：記憶要是沒刪乾淨，也不會再回味一次、長出新片段"""
        rows = self.moments_by_ref(ref)
        if rows:
            self.db.execute("DELETE FROM moments WHERE ref=?", (str(ref),))
            self.db.commit()
        return rows

    # ---------- 長期感情：片段淡去後的加總 ----------
    def raw_feelings(self, qq: str, now: float | None = None, shown: bool = False) -> dict:
        """shown：給〈我〉看、給人看的那一份——不算疙瘩的那幾種片段的芥蒂不算（見 not_grudge）。
        好感度（balance）照舊全算，10-27 用戶定好感度不動"""
        now = now or time.time()
        days = {}  # (日子, 維度, 正負) → [(影響, 淡去係數)]
        for r in self.db.execute("SELECT ts, weight, impacts, kind, feeling FROM moments WHERE qq=? AND impacts!='{}'", (qq,)):
            age = max(0.0, now - r["ts"]) / DAY
            day = datetime.fromtimestamp(r["ts"], TZ).date()
            for d, v in _clamp_impacts(json.loads(r["impacts"] or "{}")).items():
                if shown and d == "芥蒂" and v > 0 and not_grudge(r["kind"], r["feeling"]):
                    continue
                hl = HALF_LIFE[d] if d in FIXED_DECAY else HALF_LIFE[d] * (0.6 + 0.8 * r["weight"])
                days.setdefault((day, d, v > 0), []).append((v, 0.5 ** (age / hl)))
        owner = qq in core.OWNERS
        total = {d: 0.0 for d in DIMS}
        for (_, d, up), items in days.items():
            hurt = up == (d in FIXED_DECAY)  # 芥蒂、反感往上，或其他感情往下
            cap = OWNER_HURT_CAP if owner and hurt else max(DAY_CAP, max(abs(v) for v, _ in items))
            scale = min(1.0, cap / sum(abs(v) for v, _ in items))  # 超過當天上限的，按比例縮
            total[d] += sum(v * k for v, k in items) * scale
        return total

    def feelings(self, qq: str, now: float | None = None, shown: bool = False) -> dict:
        """0～100，給人看、給模型看的都從這裡換算（shown 見 raw_feelings）"""
        raw = self.raw_feelings(qq, now, shown)
        return {d: round(100 * (1 - math.exp(-max(0.0, v) / SATURATE))) for d, v in raw.items()}

    def heaviest(self, qq: str, dim: str, now: float | None = None) -> dict | None:
        """這種感情（往上的）現在分量最重、又真是 TA 惹的那段片段（類別在 REASON_KINDS、不是在心疼 TA），給「为的是」用"""
        now = now or time.time()
        best, bw = None, 0.0
        for r in self.db.execute("SELECT ts, text, impacts, kind, feeling FROM moments WHERE qq=? AND impacts!='{}'", (qq,)):
            v = _clamp_impacts(json.loads(r["impacts"] or "{}")).get(dim) or 0
            if v <= 0 or r["kind"] not in REASON_KINDS or r["feeling"] in CARE_FEELINGS:
                continue
            w = v * 0.5 ** (max(0.0, now - r["ts"]) / DAY / HALF_LIFE[dim])
            if w > bw:
                best, bw = {"ts": r["ts"], "text": r["text"]}, w
        return best

    def feel(self, qq: str, now: float | None = None) -> dict:
        """〈我〉跟 TA 的感情換成字（見 FEEL_WORDS）：line 是說話時給〈我〉看的那句，about 是話裡提到 TA 時的（不帶為什麼），
        words 是卡面短字，public 是群友看得到的（不放芥蒂、反感）"""
        f = self.feelings(qq, now, shown=True)
        got = feel_pick(f)
        good = [got[d][0] for d in DIMS if d in got and d not in FIXED_DECAY]
        bad, bad_short = [], []
        for d in DIMS:
            if d in got and d in FIXED_DECAY:
                m = self.heaviest(qq, d, now)
                bad.append(got[d][0] + (f"（为的是：{_fmt_day(m['ts'])} {m['text']}）" if m else ""))
                bad_short.append(got[d][0])
        join = lambda g, b: "；".join(x for x in ("、".join(g), "；".join(b)) if x)
        return {"line": "〈我〉跟 TA：" + join(good, bad) if got else "",
                "about": "〈我〉跟 TA：" + join(good, bad_short) if got else "",
                "words": [got[d][1] for d in DIMS if d in got],
                "public": [got[d][1] for d in DIMS if d in got and d not in FIXED_DECAY], "feelings": f}

    def balance(self, qq: str, now: float | None = None) -> dict:
        """天平：好的感情加總 對 加權後的芥蒂和反感；傾向 -100～100，正的偏好"""
        raw = self.raw_feelings(qq, now)
        good = sum(max(0.0, v) for d, v in raw.items() if d not in FIXED_DECAY)
        bad = sum(max(0.0, raw[d]) * w for d, w in BALANCE_W.items())
        return {"good": round(good, 1), "bad": round(bad, 1), "tilt": round(100 * (good - bad) / (good + bad + 1))}

    def known_days(self, qq: str, now: float | None = None) -> float:
        """認識了多久：有片段的不同日子，按 KNOWN_HALF 天淡一半加總（天天聊一週約 6.8、一個月約 26、一直聊下去最多約 87）"""
        now = now or time.time()
        days = {}
        for (ts,) in self.db.execute("SELECT ts FROM moments WHERE qq=? AND ts<=?", (qq, now)):
            d = datetime.fromtimestamp(ts, core.TZ).date()
            days[d] = max(days.get(d, 0.0), ts)
        return sum(0.5 ** ((now - ts) / 86400 / KNOWN_HALF) for ts in days.values())

    def affinity(self, qq: str, now: float | None = None) -> float:
        """好感度：傾向 × 把握 × 0.8，-80～+80，主人固定 100（10-25 從 0.5、±50 換過來）。把握＝1−e^(−(好+壞)/10)：回憶越少越靠近 0（還不熟）"""
        if qq in core.OWNERS:
            return 100.0
        b = self.balance(qq, now)
        sure = 1 - math.exp(-(b["good"] + b["bad"]) / SATURATE)
        return round(b["tilt"] * sure * AFFINITY_SCALE, 1)

    # ---------- 心裡的 TA ----------
    def get_heart(self, qq: str) -> dict | None:
        r = self.db.execute("SELECT data, updated FROM hearts WHERE qq=?", (qq,)).fetchone()
        if not r:
            return None
        try:
            return dict(json.loads(r["data"]), _updated=r["updated"])
        except Exception:
            return None

    def set_heart(self, qq: str, heart: dict, now: float | None = None):
        now = now or time.time()
        old = self.get_heart(qq) or {}
        heart = {k: v for k, v in heart.items() if not k.startswith("_")}
        hist = list(old.get("印象史") or heart.get("印象史") or [])
        if old.get("印象") and heart.get("印象") and old["印象"] != heart["印象"]:
            hist.insert(0, {"印象": old["印象"], "到": _fmt_day(now)})
        heart["印象史"] = hist[:HEART_HISTORY]
        self.db.execute("INSERT OR REPLACE INTO hearts(qq, data, updated) VALUES(?,?,?)",
                        (qq, json.dumps(heart, ensure_ascii=False), now))
        self.db.commit()

    def note_impression(self, qq: str, impression: str, now: float | None = None):
        """聊天時記下的新印象馬上換進心裡的 TA（舊的進印象史）。
        不動 updated：晚上整理時照樣看得到上次整理之後的片段和這段時間記的印象；還沒有的人先建一份只有印象的"""
        imp = (impression or "").strip()[:HEART_IMPRESSION_MAX]
        if not imp:
            return
        now = now or time.time()
        r = self.db.execute("SELECT data, updated FROM hearts WHERE qq=?", (qq,)).fetchone()
        try:
            h = json.loads(r["data"]) if r else {}
        except Exception:
            h = {}
        if h.get("印象") == imp:
            return
        hist = list(h.get("印象史") or [])
        if h.get("印象"):
            hist.insert(0, {"印象": h["印象"], "到": _fmt_day(now)})
        h["印象"], h["印象史"] = imp, hist[:HEART_HISTORY]
        self.db.execute("INSERT OR REPLACE INTO hearts(qq, data, updated) VALUES(?,?,?)",
                        (qq, json.dumps(h, ensure_ascii=False), r["updated"] if r else 0))
        self.db.commit()

    def edit(self, qq: str, field: str, action: str, value: str, now: float | None = None,
             private: bool = False) -> tuple[bool, str]:
        """〈我〉自己改心裡的 TA 的一欄（edit_heart）。和 note_impression 一樣不動 updated，晚上照樣整理上次之後的片段；
        印象改了舊的進印象史"""
        now = now or time.time()
        r = self.db.execute("SELECT data, updated FROM hearts WHERE qq=?", (qq,)).fetchone()
        try:
            h = json.loads(r["data"]) if r else {}
        except Exception:
            h = {}
        new, msg = edit_heart(h, field, action, value, private=private, now=now)
        if new is None:
            return False, msg
        if new.get("印象") != h.get("印象") and h.get("印象"):
            new["印象史"] = ([{"印象": h["印象"], "到": _fmt_day(now)}] + list(h.get("印象史") or []))[:HEART_HISTORY]
        self.db.execute("INSERT OR REPLACE INTO hearts(qq, data, updated) VALUES(?,?,?)",
                        (qq, json.dumps(new, ensure_ascii=False), r["updated"] if r else 0))
        self.db.commit()
        return True, msg

    # ---------- 態度 ----------
    def get_stance(self, qq: str) -> dict:
        r = self.db.execute("SELECT * FROM stance WHERE qq=?", (qq,)).fetchone()
        if r:
            return dict(r)
        return {"qq": qq, "attitude": 0, "offenses": 0, "last_offense": 0.0, "apologies": 0,
                "last_apology": 0.0, "changed": 0.0, "reason": ""}

    def _save_stance(self, st: dict, before: int, reason: str, now: float):
        """now 是事情發生的時間（補以前的記憶時是當時），緩和從這時候開始算"""
        if st["attitude"] != before:
            st["changed"], st["reason"] = max(st["changed"], now), reason
            core_log(self.db, st["qq"], "attitude", st["attitude"] - before, st["attitude"],
                     f"{ATTITUDES[before]}→{ATTITUDES[st['attitude']]}：{reason}", now)
        cols = [k for k in st if k != "qq"]
        self.db.execute(
            f"INSERT OR REPLACE INTO stance(qq,{','.join(cols)}) VALUES(?{',?' * len(cols)})",
            (st["qq"], *[st[c] for c in cols]))
        self.db.commit()

    def _floor(self, st: dict) -> int:
        return 1 if st["offenses"] >= FLOOR_AFTER else 0

    def apply_stance(self, qq: str, kind: str, ts: float, suggest: int = 0, note: str = "", weight: float = 0.4) -> dict:
        """一條片段對態度的影響。kind 是片段類型；ts 是事情發生的時間；suggest 是模型建議的態度（只在惡意時有用）；
        weight 是這件事有多重要"""
        now = ts
        st = self.get_stance(qq)
        before = st["attitude"]
        cap = OWNER_ATTITUDE_MAX if qq in core.OWNERS else len(ATTITUDES) - 1
        if kind == "恶意":
            if ts - st["last_offense"] >= OFFENSE_GAP:
                st["offenses"] += 1
            st["last_offense"] = max(st["last_offense"], ts)
            level = max(1, min(4, int(suggest or 1)))
            if st["offenses"] >= 2:
                level += 1  # 不是第一次了
            elif weight < SEVERE_WEIGHT:
                level = min(level, MINOR_CAP)
            st["attitude"] = min(cap, max(st["attitude"], level))
            self._save_stance(st, before, f"惡意：{note}"[:60], now)
        elif kind == "冲突":
            # 一般的鬧彆扭只記芥蒂；態度最多冷淡一下
            if st["attitude"] == 0 and suggest:
                st["attitude"] = min(cap, 1)
            self._save_stance(st, before, f"闹别扭：{note}"[:60], now)
        elif kind in ("道歉", "和好"):
            # 誠懇道歉（片段裡標成道歉的都是模型判斷過誠懇的）：降一級，以後要靠好好相處慢慢緩和。
            # 這次惡意之後還沒算過道歉，或離上次算的道歉夠久（另一天又誠懇道歉了）才算
            if ts > st["last_offense"] and (st["last_apology"] <= st["last_offense"]
                                            or ts - st["last_apology"] >= APOLOGY_GAP):
                st["apologies"] += 1
                st["last_apology"] = max(st["last_apology"], ts)
                st["attitude"] = max(self._floor(st), st["attitude"] - 1)
            self._save_stance(st, before, f"{kind}：{note}"[:60], now)
        return st

    def forgive(self, qq: str, now: float | None = None, note: str = "") -> tuple[int, int]:
        """〈我〉在聊天時決定原諒 TA 的道歉（10-28 用戶定「由她決定」）：照誠懇道歉的規則降一級。回 (原來, 現在)"""
        now = now or time.time()
        before = self.get_stance(qq)["attitude"]
        st = self.apply_stance(qq, "道歉", now, note=f"〈我〉原谅了：{note}"[:60])
        return before, st["attitude"]

    def forgive_preview(self, qq: str, now: float | None = None) -> int:
        """原諒的話會變成幾級（不寫庫）"""
        now = now or time.time()
        st = self.get_stance(qq)
        if not (now > st["last_offense"] and (st["last_apology"] <= st["last_offense"]
                                              or now - st["last_apology"] >= APOLOGY_GAP)):
            return st["attitude"]
        return max(self._floor(st), st["attitude"] - 1)

    def decided_sorry_near(self, qq: str, ts: float) -> bool:
        """ts 前後 APOLOGY_GAP 內〈我〉在聊天時已經對 TA 的道歉做過決定（原諒或沒原諒）：
        晚上回味拆出同一次道歉時，態度照她當時的決定，不再自己降一級"""
        return bool(self.db.execute(
            "SELECT 1 FROM events WHERE qq=? AND kind='sorry' AND ts BETWEEN ? AND ?",
            (qq, ts - APOLOGY_GAP, ts + APOLOGY_GAP)).fetchone())

    def ease(self, qq: str, now: float | None = None) -> dict:
        """冷淡、戒備沒再犯就慢慢緩和；冷處理以上不會自己好，要誠懇道歉"""
        now = now or time.time()
        st = self.get_stance(qq)
        before = st["attitude"]
        if before <= self._floor(st) or before >= 3:
            return st
        since = max(st["changed"], st["last_offense"])
        days = EASE_COLD_DAYS if before == 1 else EASE_DAYS.get(st["offenses"], EASE_DAYS_MORE)
        if now - since < days * DAY:
            return st
        if before == 2 and not any(
                m["kind"] in ("道歉", "和好") or m["impacts"].get("亲近", 0) > 0
                for m in self.moments(qq, 50, since=since) if m["kind"] not in ("恶意", "冲突")):
            return st  # 戒備要有好好相處的片段才緩和
        st["attitude"] = before - 1
        self._save_stance(st, before, "后来没再犯，慢慢缓和", now)
        return st

    def admin_attitude(self, qq: str, level: int, now: float | None = None) -> dict:
        now = now or time.time()
        st = self.get_stance(qq)
        before = st["attitude"]
        st["attitude"] = max(0, min(len(ATTITUDES) - 1, level))
        if st["attitude"] == 0:
            st["offenses"] = 0  # 主人出手原諒：補救機會重新算
        self._save_stance(st, before, "主人修改", now)
        return st

    def stanced(self, min_level: int = 1) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM stance WHERE attitude>=? ORDER BY attitude DESC, changed DESC", (min_level,))]

    # ---------- 其他 ----------
    def is_digested(self, doc_id: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM digested WHERE doc_id=?", (doc_id,)).fetchone())

    def digested_ids(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT doc_id FROM digested")}

    def mark_digested(self, doc_id: str, n: int):
        self.db.execute("INSERT OR REPLACE INTO digested(doc_id, ts, n) VALUES(?,?,?)", (doc_id, time.time(), n))

    def undigest(self, doc_id: str):
        """那條記憶的內容改過了（記憶更正）：當成沒讀過，下次照改過的重新回味"""
        self.db.execute("DELETE FROM digested WHERE doc_id=?", (str(doc_id),))
        self.db.commit()

    def hearts_with_moments(self) -> list[tuple[str, int]]:
        """心裡有 TA、也有片段的人：(qq, 片段總數)，最近有片段的先"""
        return [(r[0], r[1]) for r in self.db.execute(
            "SELECT h.qq, COUNT(m.id) FROM hearts h JOIN moments m ON m.qq = h.qq GROUP BY h.qq ORDER BY MAX(m.ts) DESC")]

    def set_memories(self, qq: str, mems: list[dict]):
        """只換專屬回憶這一欄。和 edit 一樣不動 updated：晚上照樣整理上次之後的片段"""
        r = self.db.execute("SELECT data, updated FROM hearts WHERE qq=?", (qq,)).fetchone()
        if not r:
            return
        try:
            h = json.loads(r["data"])
        except Exception:
            return
        h["回忆"] = clean_memories(mems)
        self.db.execute("UPDATE hearts SET data=? WHERE qq=?", (json.dumps(h, ensure_ascii=False), qq))
        self.db.commit()

    def stale_hearts(self) -> list[tuple[str, int, float]]:
        """有新片段、心裡的 TA 還沒跟上的人：(qq, 片段總數, 最重的一條)"""
        return [(r[0], r[1], r[2]) for r in self.db.execute(
            "SELECT m.qq, COUNT(*), MAX(m.weight) FROM moments m LEFT JOIN hearts h ON h.qq = m.qq "
            "GROUP BY m.qq HAVING h.updated IS NULL OR MAX(m.created) > MAX(h.updated) ORDER BY MAX(m.ts) DESC")]

    def reset(self, qq: str):
        for t in ("moments", "hearts", "stance"):
            self.db.execute(f"DELETE FROM {t} WHERE qq=?", (qq,))
        self.db.commit()


def core_log(db, qq, kind, delta, after, note, now):
    db.execute("INSERT INTO events(qq,ts,kind,delta,after,note,source) VALUES(?,?,?,?,?,?,?)",
               (qq, now, kind, delta, after, note, "heart"))


# ---------- 感情換成字 ----------
# 給〈我〉看的芥蒂不算這幾種片段（10-27 用戶定，好感度照舊全算）：
#   玩笑：被逗、被起哄時那點小委屈（1506 段裡芥蒂 179 次有 124 次是這種）
#   心事、关心，或當時的心情是心疼、担心、感动：〈我〉在聽 TA 倾诉、关心 TA，心裡不好受，不是 TA 惹了〈我〉
NOT_GRUDGE_KINDS = {"玩笑", "心事", "关心"}
CARE_FEELINGS = {"心疼", "担心", "感动"}
# 「为的是」只從這幾類挑：真是 TA 惹了〈我〉的事
REASON_KINDS = {"冲突", "恶意", "日常", "帮忙", "承诺"}


def not_grudge(kind: str, feeling: str) -> bool:
    return kind in NOT_GRUDGE_KINDS or feeling in CARE_FEELINGS

# (下限, 說話時給〈我〉看的, 卡面短字)：只講〈我〉跟 TA 是什麼樣，不寫該怎麼做；沒到最低那檔的不寫
FEEL_WORDS = {
    "亲近": ((60, "很亲", "很亲"), (30, "挺亲近", "亲近")),
    "逗趣": ((60, "天天互相逗、损来损去", "爱互损"), (30, "常开玩笑", "常开玩笑")),
    "欣赏": ((30, "挺欣赏 TA", "欣赏"),),
    "牵挂": ((30, "挂着 TA 的事", "挂心"),),
    "芥蒂": ((35, "心里对 TA 有疙瘩", "有疙瘩"), (15, "还有点小疙瘩", "小疙瘩")),
    "反感": ((35, "挺反感 TA", "反感"), (15, "有点反感 TA", "有点反感")),
}


def feel_pick(f: dict) -> dict:
    """{維度: (給〈我〉看的, 短字)}，只放到了門檻的"""
    out = {}
    for d in DIMS:
        for lo, words, short in FEEL_WORDS.get(d, ()):
            if (f.get(d) or 0) >= lo:
                out[d] = (words, short)
                break
    return out


# ---------- 給主人看 ----------
def feelings_text(f: dict) -> str:
    return " ".join(f"{d}{f[d]}" for d in DIMS if f.get(d))


# 心裡的 TA 說話時帶多少：以前截在 200 字，三分之一的人斷在半句（牵挂、语气最常被截）。
# 10-27 起按欄位挑：先保前面的，放不下的整欄不放；清單欄放得下幾項放幾項；梗只帶最近 GENG_SHOWN 條
HEART_KEEP = ("称呼", "印象", "雷区", "语气", "最近", "私下", "牵挂", "喜好", "梗", "专属回忆")
GENG_SHOWN = 3
LIST_FIELDS = ("喜好", "梗", "牵挂", "雷区")


def _heart_seg(h: dict, k: str, private: bool, memories: bool, budget, n: int | None = None) -> str:
    if k == "称呼":
        return f"叫 TA「{h['称呼']}」" if h.get("称呼") else ""
    if k in ("印象", "最近", "语气"):
        return f"{k}：{h[k]}" if h.get(k) else ""
    if k == "私下":
        p = h.get("私下") or {}
        xs = [str(x) for x in (p.get("心事") or []) + (p.get("牵挂") or [])]
        return "私下：" + "、".join(xs[:n]) if private and xs else ""
    if k == "专属回忆":
        mems = [m for m in clean_memories(h.get("回忆")) if private or not m["私聊"]] if memories else []
        return "专属回忆：" + "、".join(_memory_text(m) for m in mems[:n]) if mems else ""
    xs = h.get(k) or []
    if k == "梗":
        xs = xs[-GENG_SHOWN:] if budget else xs
        xs = [(g.get("梗", "") + (f"（{g['来历']}）" if g.get("来历") else "")) if isinstance(g, dict) else str(g) for g in xs]
    xs = [str(x) for x in xs][:n]
    return f"{k}：" + "、".join(xs) if xs else ""


def heart_fit(h: dict | None, private: bool = True, memories: bool = True, budget: int | None = None) -> tuple[str, list]:
    """心裡的 TA 寫成一段字，budget 字以內；回傳 (那段字, 這次沒放進去的欄)。budget 不給就全放（整理材料、主人看的用）"""
    if not h:
        return "", []
    order = ("称呼", "印象", "喜好", "雷区", "梗", "最近", "语气", "牵挂", "私下", "专属回忆")  # 寫出來的順序
    chosen, used, dropped = {}, 0, []
    for k in HEART_KEEP:
        seg = _heart_seg(h, k, private, memories, budget)
        if not seg:
            continue
        if budget is None or used + len(seg) + 1 <= budget:
            chosen[k], used = seg, used + len(seg) + 1
            continue
        items = seg.count("、") + 1
        for n in range(items - 1, 0, -1) if k in LIST_FIELDS + ("私下", "专属回忆") else ():
            s2 = _heart_seg(h, k, private, memories, budget, n)
            if used + len(s2) + 1 <= budget:
                chosen[k], used = s2, used + len(s2) + 1
                dropped.append(f"{k}（少了 {items - n} 项）")
                break
        else:
            dropped.append(k)
    return "；".join(chosen[k] for k in order if k in chosen), dropped


def heart_text(h: dict | None, private: bool = True, memories: bool = True, budget: int | None = None) -> str:
    """memories：帶不帶專屬回憶。說話時注入的另外單獨一行（memories_text），這裡不帶，免得被擠掉。budget 見 heart_fit"""
    return heart_fit(h, private, memories, budget)[0]


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d")


DAY_RE = re.compile(r"^\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?\s*")


def clean_memories(raw, now: float | None = None) -> list[dict]:
    """整理成 [{"事", "哪天", "私聊"}]：去掉空的、重複的，最多 MEMORY_MAX 條（新加的在後面，滿了擠掉最早的）"""
    out, seen = [], set()
    for m in raw if isinstance(raw, list) else []:
        if isinstance(m, str):
            m = {"事": m}
        if not isinstance(m, dict):
            continue
        what = str(m.get("事") or "").strip()
        day = str(m.get("哪天") or "").strip()
        got = DAY_RE.match(what)
        if got and not day:
            day, what = f"{int(got[1]):02d}-{int(got[2]):02d}", what[got.end():].strip()
        what = what[:MEMORY_WIDTH]
        if not what or what in seen:
            continue
        seen.add(what)
        priv = m.get("私聊")
        priv = priv.strip().lower() in ("true", "1", "是", "私聊") if isinstance(priv, str) else bool(priv)
        out.append({"事": what, "哪天": day[:10] or _day_of(now or time.time()), "私聊": priv})
    return out[-MEMORY_MAX:]


def _memory_text(m: dict, hide: bool = False) -> str:
    """和 moment_line 一樣事在前（見那裡的說明）；hide：群裡給她看，私聊的標出群裡別人不知道"""
    where = "私聊里，群里别人不知道" if hide and m["私聊"] else ("私聊里" if m["私聊"] else "群里")
    return f"「{m['事']}」（{m['哪天']} {where}）"


def memories_text(h: dict | None, here: str) -> str:
    """專屬回憶給〈我〉看的那一行。here：dm＝和 TA 的私聊（全部）、group＝TA 在群裡跟〈我〉說話（全部，私聊的標出來）、
    about＝話裡提到 TA（只帶不是私聊的）。沒有就空字串"""
    mems = clean_memories((h or {}).get("回忆"))
    if here == "about":
        mems = [m for m in mems if not m["私聊"]]
    if not mems:
        return ""
    def one(m):
        return _memory_text(m, hide=here == "group")
    return "〈我〉和 TA 的专属回忆（〈我〉自己挑的）：" + "；".join(one(m) for m in mems)


# 〈我〉自己改心裡的 TA（工具 lively_heart_edit，10-26）：欄位、上限和每晚整理（digest._clean_heart）一樣
HEART_FIELDS = {  # 欄名 → (種類, 最多幾條, 每條最多幾個字)
    "称呼": ("str", 1, 12), "印象": ("str", 1, HEART_IMPRESSION_MAX), "最近": ("str", 1, 30), "语气": ("str", 1, 30),
    "喜好": ("list", 4, 12), "雷区": ("list", 3, 12), "梗": ("gag", 4, 12), "牵挂": ("list", 3, 25),
    "私下牵挂": ("private", 3, 25), "私下心事": ("private", 3, 25),
    "回忆": ("memory", MEMORY_MAX, MEMORY_WIDTH),
}
HEART_ACTIONS = ("改成", "加", "删")


def _gag_text(g) -> str:
    return g.get("梗", "") if isinstance(g, dict) else str(g)


def edit_heart(h: dict | None, field: str, action: str, value: str, private: bool = False,
               now: float | None = None) -> tuple[dict | None, str]:
    """改一欄，回 (改好的心裡的 TA, 說明)；改不了回 (None, 為什麼)。不改傳進來的 h。
    private：專屬回憶新加的這條算不算私聊裡的事"""
    field = (field or "").strip().replace("私下的", "私下").replace("私下 ", "私下")
    action = (action or "").strip()
    value = (value or "").strip()
    if field not in HEART_FIELDS:
        return None, f"没有「{field}」这一栏，能改的是：" + "、".join(HEART_FIELDS)
    if action not in HEART_ACTIONS:
        return None, "action 只能是「改成」「加」「删」"
    kind, n, width = HEART_FIELDS[field]
    h = json.loads(json.dumps({k: v for k, v in (h or {}).items() if not k.startswith("_")}, ensure_ascii=False))
    if kind == "memory":
        return _edit_memories(h, action, value, private, now)
    if kind == "str":
        if action != "改成":
            return None, f"「{field}」只有一句，只能「改成」（value 写空就是清掉）"
        old = h.get(field) or ""
        h[field] = value[:width]
        return h, f"「{field}」从「{old or '（空的）'}」改成了「{h[field] or '（空的）'}」"
    if kind == "private":
        sub = field[2:]
        box = dict(h.get("私下") or {})
        items = list(box.get(sub) or [])
    else:
        items = list(h.get(field) or [])
    text = (lambda x: _gag_text(x)) if kind == "gag" else str
    if action == "删":
        if not value:
            return None, "要删哪一条：value 写那条里的字"
        hit = [x for x in items if value in text(x)]
        if not hit:
            return None, f"「{field}」里没有含「{value}」的，现在是：" + ("、".join(map(text, items)) or "（空的）")
        if len(hit) > 1 and not any(text(x) == value for x in hit):
            return None, f"「{field}」里有几条都含「{value}」：" + "、".join(map(text, hit)) + "，写全那一条"
        drop = next((x for x in hit if text(x) == value), hit[0])
        items = [x for x in items if x is not drop]
        msg = f"从「{field}」删掉了「{text(drop)}」"
    else:
        parts = [x.strip() for x in re.split(r"[、，,；;\n]+", value) if x.strip()] if action == "改成" else ([value] if value else [])
        if action == "加" and not parts:
            return None, "要加什么：value 写上"
        def one(x):
            if kind != "gag":
                return x[:width]
            gag, _, src = x.partition("（")
            return {"梗": gag.strip()[:width], "来历": src.rstrip("）").strip()[:20]}
        new = [one(x) for x in parts]
        if action == "改成":
            items, msg = new[:n], f"「{field}」改成了：" + ("、".join(map(text, new[:n])) or "（空的）")
        else:
            if any(text(x) == text(new[0]) for x in items):
                return None, f"「{field}」里已经有「{text(new[0])}」了"
            squeezed = len(items) >= n
            items = (items + new[:1])[-n:]
            msg = f"往「{field}」加了「{text(new[0])}」" + (f"（这栏最多 {n} 条，最早的那条挤掉了）" if squeezed else "")
    if kind == "private":
        box[sub] = items
        h["私下"] = box
    else:
        h[field] = items
    return h, msg


def _edit_memories(h: dict, action: str, value: str, private: bool, now: float | None) -> tuple[dict | None, str]:
    items = clean_memories(h.get("回忆"), now)
    if action == "删":
        if not value:
            return None, "要删哪一条：value 写那条里的字"
        hit = [m for m in items if value in m["事"]]
        if not hit:
            return None, "「回忆」里没有含「" + value + "」的，现在是：" + ("、".join(m["事"] for m in items) or "（空的）")
        if len(hit) > 1 and not any(m["事"] == value for m in hit):
            return None, "「回忆」里有几条都含「" + value + "」：" + "、".join(m["事"] for m in hit) + "，写全那一条"
        drop = next((m for m in hit if m["事"] == value), hit[0])
        h["回忆"] = [m for m in items if m is not drop]
        return h, f"从「回忆」删掉了{_memory_text(drop)}"
    parts = [x.strip() for x in re.split(r"[、；;\n]+", value) if x.strip()] if action == "改成" else ([value] if value else [])
    if action == "加" and not parts:
        return None, "要加什么：value 写上那件事（可以在开头写哪天，比如「10-26 …」）"
    new = clean_memories([{"事": x, "私聊": private} for x in parts], now)
    if action == "改成":
        h["回忆"] = new
        return h, "「回忆」改成了：" + ("、".join(_memory_text(m) for m in new) or "（空的）")
    if any(m["事"] == new[0]["事"] for m in items):
        return None, f"「回忆」里已经有「{new[0]['事']}」了"
    squeezed = len(items) >= MEMORY_MAX
    h["回忆"] = (items + new[:1])[-MEMORY_MAX:]
    return h, f"往「回忆」加了{_memory_text(new[0])}" + (f"（这栏最多 {MEMORY_MAX} 条，最早的那条挤掉了）" if squeezed else "")


def heart_from_card(card: dict | None, impression: str, history: list[tuple[float, str]]) -> dict:
    """第一次用：把舊的人物卡和印象搬成「心裡的 TA」"""
    card = card or {}
    h = {
        "称呼": card.get("称呼") or "",
        "印象": (impression or "")[:HEART_IMPRESSION_MAX],
        "喜好": list(card.get("喜好") or [])[:4],
        "雷区": list(card.get("雷区") or [])[:3],
        "梗": [{"梗": str(x), "来历": ""} for x in (card.get("梗") or [])][:4],
        "最近": card.get("最近") or "",
        "语气": card.get("相处") or "",
        "牵挂": [],
        "私下": {"牵挂": [], "心事": []},
    }
    seen = {h["印象"]}
    hist = []
    for ts, imp in history:
        if imp and imp not in seen:
            seen.add(imp)
            hist.append({"印象": imp, "到": _fmt_day(ts)})
    h["印象史"] = hist[:HEART_HISTORY]
    return h


# ---------- 說話時想起哪幾段 ----------
PICK_POOL = 60  # 從最近這麼多段裡挑
PICK_FRESH_DAYS = 30  # 沒有沾邊的時候，只帶這麼多天內的一段
PICK_SKIP = {"冲突", "恶意"}  # 沒沾邊時不主動翻出來的
_GRAM_STOP = set("的了吗呢啊呀吧嘛哦哈是我你他她它们在有就也都和跟这那个不没么什怎样")


def _grams(text: str) -> set[str]:
    t = re.sub(r"[\s\W_]+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1) if not (set(t[i:i + 2]) <= _GRAM_STOP)}


def pick_moments(moments: list[dict], text: str, n: int = 2, now: float | None = None) -> list[dict]:
    """和這句話有兩個字一樣的片段優先（越多越前、同分新的在前）；一段都沒沾邊就只帶最近一段平常的事"""
    if n <= 0 or not moments:
        return []
    now = now or time.time()
    want = _grams(text)
    scored = [(len(want & _grams(m.get("text", ""))), m) for m in moments]
    hit = sorted((x for x in scored if x[0]), key=lambda x: (-x[0], -(x[1].get("ts") or 0)))
    if hit:
        return [m for _, m in hit[:n]]
    fresh = [m for m in moments if m.get("kind") not in PICK_SKIP and now - (m.get("ts") or 0) <= PICK_FRESH_DAYS * 86400]
    return sorted(fresh, key=lambda m: -(m.get("ts") or 0))[:1]


def moment_line(m: dict, hide: bool = False) -> str:
    """給〈我〉看的一段片段：事在前、哪天在哪放括號裡。10-28 以前寫成「10-28 群里：…」，她照著這個樣子把 |记住 寫成了正文裡的一行發出去"""
    where = ("私聊里，群里别人不知道" if hide else "私聊里") if m.get("private") else "群里"
    return f"「{m.get('text', '')}」（{_fmt_day(m.get('ts') or 0)} {where}）"
