"""群裡的事（10-28 起，情感第三階段的第一步）：群梗、群友之間的關係。

每晚回味群聊記憶時順手寫下（digest.GROUP_RULE），**先只記在後台**：主人用 /好感 群梗、/好感 关系 看，
看準了才帶給〈我〉（10-28 她自己選「两个都记」，用戶定先只記錄）。和 bond.db 共用連線
"""
import time
from datetime import datetime

from .core import TZ

GAG_MAX, ORIGIN_MAX, HOW_MAX = 16, 30, 30


class GroupNotes:
    def __init__(self, db):
        self.db = db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS group_gags (
                gid TEXT, gag TEXT, origin TEXT DEFAULT '', who TEXT DEFAULT '[]',
                first REAL, last REAL, n INTEGER DEFAULT 1, PRIMARY KEY (gid, gag));
            CREATE TABLE IF NOT EXISTS pairs (
                a TEXT, b TEXT, gid TEXT, how TEXT, ts REAL, n INTEGER DEFAULT 1, ref TEXT DEFAULT '',
                PRIMARY KEY (a, b, gid));
        """)
        self.db.commit()

    def note_gag(self, gid: str, gag: str, origin: str = "", who=None, ts: float | None = None) -> bool:
        import json
        gag = (gag or "").strip()[:GAG_MAX]
        if not gid or not gag:
            return False
        ts = ts or time.time()
        who = [str(x) for x in (who or []) if str(x).isdigit()][:6]
        r = self.db.execute("SELECT who, first, last, n FROM group_gags WHERE gid=? AND gag=?", (gid, gag)).fetchone()
        if r:
            old = json.loads(r[0] or "[]")
            merged = list(dict.fromkeys(old + who))[:8]
            self.db.execute("UPDATE group_gags SET who=?, first=?, last=?, n=n+1, origin=CASE WHEN origin='' THEN ? ELSE origin END "
                            "WHERE gid=? AND gag=?", (json.dumps(merged), min(r[1], ts), max(r[2], ts),
                                                    (origin or "").strip()[:ORIGIN_MAX], gid, gag))
        else:
            self.db.execute("INSERT INTO group_gags(gid, gag, origin, who, first, last) VALUES(?,?,?,?,?,?)",
                            (gid, gag, (origin or "").strip()[:ORIGIN_MAX], json.dumps(who), ts, ts))
        return True

    def note_pair(self, a: str, b: str, gid: str, how: str, ts: float | None = None, ref: str = "") -> bool:
        a, b = str(a or "").strip(), str(b or "").strip()
        how = (how or "").strip()[:HOW_MAX]
        if not (a.isdigit() and b.isdigit()) or a == b or not how or not gid:
            return False
        a, b = sorted((a, b))
        ts = ts or time.time()
        r = self.db.execute("SELECT ts FROM pairs WHERE a=? AND b=? AND gid=?", (a, b, gid)).fetchone()
        if r and r[0] > ts:  # 補以前的記憶時，舊的不蓋新的
            self.db.execute("UPDATE pairs SET n=n+1 WHERE a=? AND b=? AND gid=?", (a, b, gid))
        else:
            self.db.execute("INSERT INTO pairs(a, b, gid, how, ts, ref) VALUES(?,?,?,?,?,?) "
                            "ON CONFLICT(a, b, gid) DO UPDATE SET how=excluded.how, ts=excluded.ts, ref=excluded.ref, n=n+1",
                            (a, b, gid, how, ts, ref))
        return True

    def gags(self, gid: str = "", limit: int = 20) -> list[dict]:
        import json
        sql = "SELECT * FROM group_gags" + (" WHERE gid=?" if gid else "") + " ORDER BY last DESC LIMIT ?"
        out = []
        for r in self.db.execute(sql, ((gid, limit) if gid else (limit,))):
            d = dict(r)
            d["who"] = json.loads(d["who"] or "[]")
            out.append(d)
        return out

    def pairs_of(self, qq: str, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM pairs WHERE a=? OR b=? ORDER BY ts DESC LIMIT ?", (qq, qq, limit))]


def day(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d")
