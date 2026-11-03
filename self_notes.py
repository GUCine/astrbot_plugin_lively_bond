"""〈我〉自己的事（10-30 用戶定，方案在私人庫 設計/她自己的事-1030.md）。

起因：10-30 09:50 群裡〈主人〉說想買台 mac mini 裝她，她接「那本鱼就有新家了」，〈主人〉回「你现在已经在我台式机了啊」。
〈主人〉之前跟她說過，但那句只留在某個聊天的歷史裡，壓縮時丟了；長期記憶裡也沒有。她記東西的地方都不是關於她自己的：
心裡的 TA、记住是別人，主人交代的事是主人的規矩，記事本是待辦，人設她改不了。

這裡給她一本自己記、自己改的本子（lively_self_note）：住在哪、手上有什麼、最近自己身上變了什麼。每一輪都帶（接在 system 最後），
心跳那頁也帶。誰說的都能記，記不記她判斷；每條記下是誰告訴她的，不是〈主人〉說的另外記一筆給主人私聊（提不提她定）。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
TEXT_MAX = 150  # 一條多長（10-30 從 60 放寬）
ALL_MAX = 800  # 加起來多長：每一輪都帶（10-30 從 400 放寬）
COUNT_MAX = 20  # 一共記多少條
SELF = "self"  # told_by：她自己知道的

SCHEMA = """
CREATE TABLE IF NOT EXISTS self_notes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    updated REAL NOT NULL,
    src TEXT DEFAULT '',
    told_by TEXT DEFAULT '',
    told_name TEXT DEFAULT ''
)"""

BLOCK_HEAD = "〈我〉自己记着的自己的事（〈我〉用 lively_self_note 记的，记错了、过时了可以改、删）："


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts or 0, TZ).strftime("%m-%d")


class SelfNotes:
    def __init__(self, db):
        self.db = db
        db.execute(SCHEMA)
        db.commit()

    @staticmethod
    def _row(r) -> dict:
        return {"id": int(r[0]), "text": r[1], "ts": r[2], "updated": r[3], "src": r[4] or "",
                "told_by": r[5] or "", "told_name": r[6] or ""}

    def all(self) -> list[dict]:
        return [self._row(r) for r in self.db.execute(
            "SELECT id,text,ts,updated,src,told_by,told_name FROM self_notes ORDER BY id")]

    def get(self, nid) -> dict | None:
        try:
            nid = int(str(nid).strip().lstrip("Ss自").strip() or 0)
        except ValueError:
            return None
        r = self.db.execute("SELECT id,text,ts,updated,src,told_by,told_name FROM self_notes WHERE id=?", (nid,)).fetchone()
        return self._row(r) if r else None

    def total(self, skip: int = 0) -> int:
        return sum(len(n["text"]) for n in self.all() if n["id"] != skip)

    def check(self, text: str, skip: int = 0) -> str:
        """記得下就回空字串，不然回原因（事實）"""
        if not text:
            return "没写记的是什么"
        if len(text) > TEXT_MAX:
            return f"一条最多 {TEXT_MAX} 个字，这条有 {len(text)} 个字"
        if not skip and len(self.all()) >= COUNT_MAX:
            return f"本子上已经有 {COUNT_MAX} 条了，先删掉过时的，或者把几条合成一条"
        now = self.total(skip)
        if now + len(text) > ALL_MAX:
            return (f"本子每一轮都会带着，加起来最多 {ALL_MAX} 个字；现在已经有 {now} 个字，"
                    f"再加这条 {len(text)} 个字就超了。可以先删掉过时的，或者把几条合成一条")
        return ""

    def add(self, text: str, src: str = "", told_by: str = "", told_name: str = "", now: float | None = None) -> dict:
        now = now or time.time()
        cur = self.db.execute("INSERT INTO self_notes(text,ts,updated,src,told_by,told_name) VALUES(?,?,?,?,?,?)",
                              (text, now, now, src, told_by, told_name))
        self.db.commit()
        return self.get(cur.lastrowid)

    def edit(self, nid: int, text: str, now: float | None = None) -> dict:
        self.db.execute("UPDATE self_notes SET text=?, updated=? WHERE id=?", (text, now or time.time(), int(nid)))
        self.db.commit()
        return self.get(nid)

    def drop(self, nid: int) -> dict | None:
        n = self.get(nid)
        if n:
            self.db.execute("DELETE FROM self_notes WHERE id=?", (n["id"],))
            self.db.commit()
        return n


def told_text(n: dict, is_owner=None) -> str:
    """誰告訴她的，給她看：不寫號碼"""
    by = n.get("told_by") or ""
    if by == SELF:
        return "〈我〉自己知道的"
    if by and is_owner and is_owner(by):
        return "〈主人〉说的"
    if by:
        return f"{n.get('told_name') or '一个人'}说的"
    return ""


def note_line(n: dict, is_owner=None) -> str:
    bits = [f"{_day(n['ts'])} 记"]
    if n.get("updated", 0) > n.get("ts", 0) + 60:
        bits.append(f"{_day(n['updated'])} 改过")
    if t := told_text(n, is_owner):
        bits.append(t)
    return f"S{n['id']} {n['text']}（{'，'.join(bits)}）"


def notes_block(notes: list[dict], is_owner=None) -> str:
    """每一輪帶給她的那段；本子空的回空字串"""
    if not notes:
        return ""
    return BLOCK_HEAD + "\n" + "\n".join(note_line(n, is_owner) for n in notes)
