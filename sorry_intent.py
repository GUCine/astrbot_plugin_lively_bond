"""TA 這句話像不像在道歉（10-28，情感第二階段「補救」）：只對〈我〉現在對 TA 不是正常態度的人看。

做法照印象卡（card_intent.py）：先看字眼，沾得上才算向量（bge-m3），和道歉例句夠像、又比最像的反例高出一截才算像。
像的話只把事實告訴〈我〉，原不原諒由〈我〉在標記裡寫 |原谅（10-28 用戶定「由她決定」），這裡不判誠不誠懇
"""
import math
import re

from astrbot.api import logger

DEFAULT_YES = ["对不起", "我错了", "刚才是我不对", "抱歉，我不该那样说你", "别生气了，是我过分了", "我跟你道歉",
               "原谅我好不好", "对不起嘛，我以后不这样了", "我刚才说话太冲了，对不起", "不好意思，冒犯到你了"]
DEFAULT_NO = ["对不起我来晚了", "不好意思借过一下", "你给我道歉", "你错了", "我没错", "道歉有用的话要警察干嘛",
              "他该跟你道歉", "抱歉打扰一下，问个问题", "不好意思刚才没看到消息", "你怎么不道歉"]
KEYWORDS = ["对不起", "抱歉", "sorry", "错了", "不对", "道歉", "不好意思", "原谅", "别生气", "消消气", "赔罪", "赔不是",
            "我的错", "冒犯", "过分", "不该", "认错", "对不住"]
MAX_LEN = 80


def _cos(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class SorryIntent:
    def __init__(self, context, settings):
        self.context, self.s = context, settings
        self._vecs: dict[str, list[float]] = {}

    def examples(self) -> tuple[list[str], list[str]]:
        return ([str(x) for x in (self.s.get("sorry_examples") or DEFAULT_YES)],
                [str(x) for x in (self.s.get("sorry_not_examples") or DEFAULT_NO)])

    @staticmethod
    def clean(text: str) -> str:
        text = re.sub(r"\[(?:At|引用|图片|表情|回复)[^\]]*\]", "", text or "")
        return re.sub(r"\s+", "", text).strip("，,。.！!～~ ")

    def maybe(self, text: str) -> bool:
        low = text.lower()
        return any(k in low for k in KEYWORDS)

    def _provider(self):
        pid = self.s.get("embedding_provider_id", "")
        provs = list(self.context.get_all_embedding_providers() or [])
        for p in provs:
            if pid and p.provider_config.get("id") == pid:
                return p
        return provs[0] if provs else None

    async def check(self, text: str) -> tuple[bool, float, float]:
        """返回 (像不像在道歉, 和道歉例句最像的分, 和反例最像的分)"""
        text = self.clean(text)
        if not text or len(text) > MAX_LEN or not self.maybe(text):
            return False, 0.0, 0.0
        prov = self._provider()
        if prov is None:
            raise RuntimeError("没有可用的向量模型")
        yes, no = self.examples()
        missing = [t for t in yes + no if t not in self._vecs]
        if missing:
            for t, v in zip(missing, await prov.get_embeddings(missing)):
                self._vecs[t] = v
        v = await prov.get_embedding(text)
        sy = max((_cos(v, self._vecs[x]) for x in yes), default=0.0)
        sn = max((_cos(v, self._vecs[x]) for x in no), default=0.0)
        hit = sy >= float(self.s.get("sorry_min_sim", 0.70)) and sy - sn >= float(self.s.get("sorry_margin", 0.05))
        logger.info(f"[lively_bond] 道歉判断 {'像' if hit else '不像'}：像道歉 {sy:.2f}，像反例 {sn:.2f}「{text[:30]}」")
        return hit, sy, sn
