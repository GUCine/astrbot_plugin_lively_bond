"""判斷一句話是不是在要「機器人對我的印象卡」：語義比對 + 自己學新說法。

- 用向量模型把這句話和兩組例句比：「要卡」的例句和「不是要卡」的例句（問別人、誇機器人、別的卡、評價別的東西……）。
  向量模型分得清話題，分不太清是誰問誰，所以要有反例：和要卡的例句夠像（>= min_sim），
  而且比最像的反例還高出 margin，才算在要卡
- 自己學：模型自己調了印象卡工具、而這句話沒被認出來，就把它記成新的要卡例句；主人可以用指令刪、加、標成不算
- 學到的例句存在 bond.db 的 meta 裡，配置裡的是起步例句
"""
import json
import math
import re

from astrbot.api import logger

DEFAULT_YES = ["你对我的印象怎么样", "给我看看我的印象卡", "我在你心里是什么样的人", "你觉得我这个人怎么样",
               "发一下我的卡片", "说说你对我的看法"]
DEFAULT_NO = ["你对他的印象怎么样", "你觉得她这个人怎么样", "我对你的印象很好", "我的卡丢了", "你觉得这个东西怎么样",
              "我觉得你很可爱", "在我的印象里是这样的", "帮我评价一下这个"]
LEARN_KEY = "card_examples_learned"  # {"yes": [...], "no": [...]}
MAX_LEARNED = 40
MAX_LEN = 60  # 太長的話不像在要卡，也不比了（長句的向量會被別的內容沖淡）
CQ_RE = re.compile(r"\[(?:At|引用|图片|表情|回复)[^\]]*\]")


def _cos(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class CardIntent:
    def __init__(self, context, settings, store):
        self.context, self.s, self.store = context, settings, store
        self._vecs: dict[str, list[float]] = {}

    # ---------- 例句 ----------
    def learned(self) -> dict:
        try:
            d = json.loads(self.store.get_meta(LEARN_KEY) or "{}")
        except Exception:
            d = {}
        return {"yes": list(d.get("yes", [])), "no": list(d.get("no", []))}

    def _save_learned(self, d: dict):
        self.store.set_meta(LEARN_KEY, json.dumps(d, ensure_ascii=False))

    def examples(self) -> tuple[list[str], list[str]]:
        d = self.learned()
        yes = [str(x) for x in (self.s.get("card_examples") or DEFAULT_YES)] + d["yes"]
        no = [str(x) for x in (self.s.get("card_not_examples") or DEFAULT_NO)] + d["no"]
        return yes, no

    def clean(self, text: str) -> str:
        text = CQ_RE.sub("", text or "")
        name = self.s.bot_name()
        if name:
            text = text.replace(f"@{name}", "").replace(name, "")
        return re.sub(r"\s+", "", text).strip("，,。.！!～~ ")

    def add(self, text: str, yes: bool = True) -> str:
        text = self.clean(text)
        if not text or len(text) > MAX_LEN:
            return ""
        d = self.learned()
        mine, other = ("yes", "no") if yes else ("no", "yes")
        if text in d[mine]:
            return text
        d[other] = [x for x in d[other] if x != text]
        d[mine] = (d[mine] + [text])[-MAX_LEARNED:]
        self._save_learned(d)
        return text

    def remove(self, n: int) -> str | None:
        """按「卡片例句」列表裡的編號刪學到的例句（要卡的在前，不算的在後）"""
        d = self.learned()
        both = [("yes", x) for x in d["yes"]] + [("no", x) for x in d["no"]]
        if not 1 <= n <= len(both):
            return None
        kind, text = both[n - 1]
        d[kind] = [x for x in d[kind] if x != text]
        self._save_learned(d)
        return text

    # ---------- 比對 ----------
    def _provider(self):
        pid = self.s.get("embedding_provider_id", "")
        provs = list(self.context.get_all_embedding_providers() or [])
        for p in provs:
            if pid and p.provider_config.get("id") == pid:
                return p
        return provs[0] if provs else None

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        prov = self._provider()
        if prov is None:
            raise RuntimeError("没有可用的向量模型")
        missing = [t for t in texts if t not in self._vecs]
        if missing:
            for t, v in zip(missing, await prov.get_embeddings(missing)):
                self._vecs[t] = v
        return [self._vecs[t] for t in texts]

    async def check(self, text: str) -> tuple[bool, float, float]:
        """返回 (是不是在要卡, 和要卡例句最像的分, 和反例最像的分)"""
        text = self.clean(text)
        if not text or len(text) > MAX_LEN:
            return False, 0.0, 0.0
        yes, no = self.examples()
        vecs = await self._embed(yes + no)
        prov = self._provider()
        v = await prov.get_embedding(text)  # 使用者的話每次都不一樣，不進快取
        sy = max((_cos(v, x) for x in vecs[:len(yes)]), default=0.0)
        sn = max((_cos(v, x) for x in vecs[len(yes):]), default=0.0)
        min_sim = float(self.s.get("card_min_sim", 0.70))
        margin = float(self.s.get("card_margin", 0.05))
        hit = sy >= min_sim and sy - sn >= margin
        if hit or sy >= min_sim - 0.05:
            logger.info(f"[lively_bond] 印象卡判断 {'要' if hit else '不要'}：像要卡 {sy:.2f}，像反例 {sn:.2f}「{text[:30]}」")
        if len(self._vecs) > 500:  # 例句改來改去時別無限長
            keep = set(yes + no)
            self._vecs = {k: x for k, x in self._vecs.items() if k in keep}
        return hit, sy, sn
