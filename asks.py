"""〈主人〉交代的事（10-28 用戶定）。

起因：10-28 18:10 主人私聊交代「20000001、1785079935 是你爹我的仇人，遇见了就阴阳怪气他」，她回「记下了」，
實際只記成主人身上的一段「记住」（沒號碼、只在主人說話時帶）；那兩個人在群裡跟她說話時她什麼都看不到。
11:47「有人反复念叨没人陪，就别再劝他去现实里找人」也只在主人私聊的長期記憶裡，換個聊天召回不到。

這裡給她一本隨身帶著的本子：只有〈主人〉說話的聊天裡能記、改、刪（她用 lively_owner_rule，寫的是她自己的話），
每條掛在某些人（qq:號）、某些群（group:號），或到哪都算（all）。說話時帶給她的是事實「〈主人〉交代过：…」，
照不照做、怎麼做是她（和人設、分寸）的事，程式不攔不逼。

11-02：交代裡寫「今天」「今晚」「明天」這種說哪一天的，記成那天的事，那天過完（隔天早上 6 點，見 DAY_START）就不再帶給她，
本子上留著、標「那天的事」。起因：10-31 22:16 主人說「今天几个群的群漫画都要重新走流程做一遍发出去，纯良那个只发下午画的那张」，
她記成到哪都算；過了午夜她把「今天」當成 11-02，以為下午那張還沒發，11-02 00:17 把一張說說的自拍發進了純良群。
「从今天起」「今天开始」這種說從此以後的不算。到期時間存在 until 欄（記、改的時候按當時的時間算），記憶插件搬交代時跳過到期的。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
TEXT_MAX = 120  # 一條多長
ALL_MAX = 300  # 到哪都算的加起來多長：每輪都帶，太長了擠別的
COUNT_MAX = 60  # 一共記多少條
ALL = "all"
DAY_START = 6  # 一天從早上幾點算起：半夜一點說的「今天」多半還是前一晚那一天
# 說的是哪一天：今天這類 +0、明天 +1、后天 +2；「从今天起」「今天开始／以后」是從此以後，不算
_NOT_ONCE = r"(?!\s*(?:起|开始|以后|往后|之后|以来))"
DAY_WORDS = ((re.compile(r"(?<!从)(?:今天|今日|今晚|今夜|今早|今儿|当天)" + _NOT_ONCE), 0),
             (re.compile(r"(?<!从)(?:明天|明日|明早|明晚)" + _NOT_ONCE), 1),
             (re.compile(r"(?<!从)(?:后天)" + _NOT_ONCE), 2))

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_asks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    targets TEXT NOT NULL,
    ts REAL NOT NULL,
    updated REAL NOT NULL,
    src TEXT DEFAULT ''
)"""


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts or 0, TZ).strftime("%m-%d")


def until_of(text: str, at: float) -> float | None:
    """這條交代說的是哪天的事：那天過完（隔天 DAY_START 點）的時間；不是哪一天的事回 None"""
    off = max((d for rx, d in DAY_WORDS if rx.search(text or "")), default=None)
    if off is None:
        return None
    day = (datetime.fromtimestamp(at, TZ) - timedelta(hours=DAY_START)).date()
    end = datetime(day.year, day.month, day.day, DAY_START, tzinfo=TZ) + timedelta(days=off + 1)
    return end.timestamp()


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d %H:%M")


def norm_targets(targets) -> list[str]:
    """qq:號 / group:號 / all，去重、照原順序；all 和別的一起寫就只留 all"""
    out = []
    for t in targets or []:
        t = str(t or "").strip()
        if t == ALL or (t.split(":", 1)[0] in ("qq", "group") and t.split(":", 1)[-1].isdigit()):
            if t not in out:
                out.append(t)
    return [ALL] if ALL in out or not out else out


class OwnerAsks:
    def __init__(self, db):
        self.db = db
        db.execute(SCHEMA)
        cols = [r[1] for r in db.execute("PRAGMA table_info(owner_asks)")]
        if "until" not in cols:  # 11-02 加的：舊的交代按最後一次記、改的時間補算一次
            db.execute("ALTER TABLE owner_asks ADD COLUMN until REAL")
            for aid, text, upd in db.execute("SELECT id,text,updated FROM owner_asks").fetchall():
                db.execute("UPDATE owner_asks SET until=? WHERE id=?", (until_of(text, upd), aid))
        db.commit()

    @staticmethod
    def _row(r) -> dict:
        try:
            targets = json.loads(r[2])
        except Exception:
            targets = [ALL]
        return {"id": int(r[0]), "text": r[1], "targets": norm_targets(targets), "ts": r[3], "updated": r[4], "src": r[5] or "",
                "until": r[6]}

    def all(self) -> list[dict]:
        """本子上全部（含那天的事已經過了的）；說話時帶的用 live()"""
        return [self._row(r) for r in self.db.execute(
            "SELECT id,text,targets,ts,updated,src,until FROM owner_asks ORDER BY id")]

    def live(self, now: float | None = None) -> list[dict]:
        """還算數的：不是哪一天的事，或那天還沒過完"""
        now = time.time() if now is None else now
        return [a for a in self.all() if not a["until"] or a["until"] > now]

    def get(self, aid) -> dict | None:
        try:
            aid = int(str(aid).strip().lstrip("交代").strip() or 0)
        except ValueError:
            return None
        r = self.db.execute("SELECT id,text,targets,ts,updated,src,until FROM owner_asks WHERE id=?", (aid,)).fetchone()
        return self._row(r) if r else None

    def for_person(self, qq) -> list[dict]:
        key = f"qq:{qq}"
        return [a for a in self.live() if key in a["targets"]]

    def for_group(self, gid) -> list[dict]:
        key = f"group:{gid}"
        return [a for a in self.live() if key in a["targets"]]

    def everywhere(self) -> list[dict]:
        return [a for a in self.live() if a["targets"] == [ALL]]

    def _all_len(self, skip: int = 0) -> int:
        return sum(len(a["text"]) for a in self.everywhere() if a["id"] != skip)

    def check(self, text: str, targets: list[str], skip: int = 0) -> str:
        """記得下就回空字串，不然回原因（事實）"""
        if not text:
            return "没写交代的是什么"
        if len(text) > TEXT_MAX:
            return f"一条最多 {TEXT_MAX} 个字，这条有 {len(text)} 个字"
        if targets == [ALL] and self._all_len(skip) + len(text) > ALL_MAX:
            return (f"「到哪都算」的交代每一轮都会带着，加起来最多 {ALL_MAX} 个字；现在已经有 {self._all_len(skip)} 个字，"
                    f"再加这条 {len(text)} 个字就超了。可以和〈主人〉商量把几条合成一条、删掉不用的，或者挂到具体的人、群上")
        if not skip and len(self.all()) >= COUNT_MAX:
            return f"本子上已经有 {COUNT_MAX} 条了，先删掉不用的"
        return ""

    def add(self, text: str, targets, src: str = "", now: float | None = None) -> dict:
        now = now or time.time()
        targets = norm_targets(targets)
        cur = self.db.execute("INSERT INTO owner_asks(text,targets,ts,updated,src,until) VALUES(?,?,?,?,?,?)",
                              (text, json.dumps(targets), now, now, src, until_of(text, now)))
        self.db.commit()
        return self.get(cur.lastrowid)

    def edit(self, aid: int, text: str | None = None, targets=None, now: float | None = None) -> dict:
        a = self.get(aid)
        text = a["text"] if text is None else text
        targets = a["targets"] if targets is None else norm_targets(targets)
        now = now or time.time()
        self.db.execute("UPDATE owner_asks SET text=?, targets=?, updated=?, until=? WHERE id=?",
                        (text, json.dumps(targets), now, until_of(text, now), a["id"]))
        self.db.commit()
        return self.get(a["id"])

    def drop(self, aid: int) -> dict | None:
        a = self.get(aid)
        if a:
            self.db.execute("DELETE FROM owner_asks WHERE id=?", (a["id"],))
            self.db.commit()
        return a


def ask_line(a: dict) -> str:
    """一條交代，給她看：哪天、在私聊還是群裡交代的（群裡交代的群友也聽見了）"""
    where = "群里" if str(a.get("src") or "").startswith("group") else "私聊里"
    return f"「{a['text']}」（交代{a['id']}，{_day(a['ts'])} {where}{'；' + until_note(a) if a.get('until') else ''}）"


def until_note(a: dict, now: float | None = None) -> str:
    """說的是哪天的事時給她看的一句：到什麼時候還帶著／已經過了"""
    u = a.get("until")
    if not u:
        return ""
    now = time.time() if now is None else now
    return (f"说的是那天的事，{_hm(u)} 起不再带" if u > now else f"说的是那天的事，{_hm(u)} 过了，不再带")


def where_text(a: dict, name_of=None, group_name=None) -> str:
    """掛在哪：給工具回傳和面板看（帶號碼，她自己的工具結果裡看得到號碼沒關係）"""
    if a["targets"] == [ALL]:
        return "到哪都算"
    bits = []
    for t in a["targets"]:
        kind, num = t.split(":", 1)
        if kind == "qq":
            n = name_of(num) if name_of else ""
            bits.append(f"QQ {num}" + (f"（{n}）" if n else ""))
        else:
            n = group_name(num) if group_name else ""
            bits.append(f"群 {num}" + (f"（{n}）" if n else ""))
    return "、".join(bits)


def asks_block(about_speaker: list[dict], speaker_name: str, in_group: list[dict], everywhere: list[dict]) -> str:
    """說話時帶給她的那段：正在說話的人、這個群、到哪都算。都沒有回空字串"""
    lines = []
    for a in about_speaker:
        lines.append(f"- 关于正在说话的「{speaker_name or 'TA'}」：{ask_line(a)}")
    for a in in_group:
        lines.append(f"- 在这个群：{ask_line(a)}")
    for a in everywhere:
        lines.append(f"- 到哪都算：{ask_line(a)}")
    if not lines:
        return ""
    return ("[系统提示，不要复述] 〈主人〉跟〈我〉交代过的事（〈我〉用 lively_owner_rule 记下的；"
            "私聊里交代的别人不知道）：\n" + "\n".join(lines))
