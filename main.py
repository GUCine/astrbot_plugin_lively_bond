"""機器人的好感度：熟悉度、好感、心情、印象、好久不見，以及機器人自己此刻的狀態。

情感：每晚從 LivingMemory 的記憶裡回味出和每個人的片段，由片段算出長期感情（親近、逗趣、牽掛、欣賞、芥蒂、反感；說話時換成「〈我〉跟 TA：…」一句字），
整理「心裡的 TA」（稱呼、印象、梗、語氣、牽掛；私聊裡知道的事只放「私下」），記下態度（正常、冷淡、戒備、冷處理、封閉）。
主人可以用指令查看；說話時用的是好感度（relation.py）、態度、心情和心裡的 TA（還沒有的人帶聊天時記的印象），
開了 inject_heart 再帶一兩段跟這句話沾邊的片段。

情感第二階段（2.2.0，10-28）：
- 「记住」：好感標記第三段，她覺得以後還會想起來的事當場記成片段（source=live，只記事、不帶分數），TA 再說話時帶上這一天記的最多 3 條；
  標記裡的「印象」不再當場改心裡的 TA（她選的，見 remember 設定）
- 道歉：她對 TA 不是正常態度時，TA 像在道歉（sorry_intent.py）就照實告訴她，原不原諒她寫 |原谅 自己定（用戶定「由她決定」），不判誠不誠懇
- 態度只描述＋建議（可以用 lively_skip_reply 不回），程式不攔（用戶定）；回味時有人到了封閉，交給她寫一句告訴主人
- 群梗、群友之間（group.py）：每晚回味群聊記憶時順手記，只在後台（/好感 群梗、/好感 关系）

關係讀口：其他插件問「〈我〉對 TA 怎麼樣」用 relation(qq) / relations()（見 relation.py），不要自己讀 bond.db

每週人物卡 10-24 停了：和心裡的 TA 欄位重疊、兩份各寫各的（雷區對不上），說話只讀心裡的 TA。
cards 表留著當舊資料，停之前只有卡的人搬進心裡的 TA（_retire_cards）
"""
import asyncio
import re
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Plain
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr

from . import core
from .core import IMPRESSION_MAX, TZ, BondStore, build_prompt, clean_mood_word, clean_remember, has_tag, mood_shown, narrated_tail, parse_tag, remember_lines_of, stage, strip_tags
from . import card_img
from .card_intent import CardIntent
from .digest import DOCS_PER_RUN, Digest
from .heart import (ATTITUDES, PICK_POOL, HeartStore, edit_heart, feelings_text, heart_from_card, heart_text, memories_text, moment_line,
                    pick_moments)
from .group import GroupNotes, day as gday
from .lively_common import PLUGIN_DATA, Settings, note_outbox, tell_owner
from .sorry_intent import SorryIntent
from .relation import ENEMY_WORD, FEED_TIERS, Relations, bot_text, vendor_key
from .asks import ALL, OwnerAsks, asks_block, until_note, where_text
from .self_notes import ALL_MAX as SELF_MAX, SELF, SelfNotes, note_line, notes_block, told_text

# record：只記錄不影響說話（試執行）；active：把關係狀態告訴模型
MODE_KEY = "mode"
NOTE_RE = re.compile(r"<bond_note>.*?(?:</bond_note>|$)", re.S)
EASE_EVERY = 3600  # 態度多久看一次能不能緩和（不等夜裡回味）
FOLLOW_GAP = 600  # 這個聊天 10 分鐘內剛發過卡，再有人要多半是跟著要的（「我呢」），不學成例句
# 同一個人隔多久能再要一張由她定（10-28 用戶拿掉了 10 分鐘那道）；只擋同一條消息、正在發的那張
# 沒發成時照實告訴她 TA 那邊什麼都沒收到、幾點能再發（10-28 用戶定：告訴她原因）；怎麼跟 TA 說她定
CARD_NOT_SENT = "卡没发出去，TA 那边什么也没收到（〈我〉这一轮的话还没发）"
CARD_CHECK = (3, 10, 20)  # 發卡報超時以後，隔這幾秒翻一次那邊的消息看到了沒有
MENTION_RE = re.compile(r"(?<![\d.])[1-9]\d{4,10}(?![\d.])")  # 話裡單獨寫出來的號碼（QQ 號、群號）
# 在要自己的印象卡（語義判斷見 card_intent.py）：card_ask 開著時只提醒〈我〉、發不發她自己定（10-26 她說卡「不经本鱼点头就出去了」）；
# 提醒裡要寫明「光說喏卡不會出去」：重放時沒這句，她偶爾只回「喏，自己看」不調工具。card_ask 關掉是舊做法：語義對上就直接發
CARD_ASK_HINT = ("[系统提示，不要复述] TA 这句话像是想看〈我〉对 TA 的印象卡（一张图片，上面有好感）。发不发〈我〉自己定："
                 "想给 TA 看，就调用 lively_impression_card，卡片才会发出去——光说「喏，自己看」「给你看」卡是不会出去的；"
                 "觉得 TA 其实不是在要、或者不想给，就照常说话，不用解释为什么没发")
# 提醒過、她嘴上說了要給看（「喏，自己看」）卻沒調工具：卡沒出去，TA 只看到一句空話（11-02 11:38 云亦七要好感度就這樣）。
# 她話裡明說了在給，就替她把這張補上；話裡是不給、不想給的不補
CARD_GIVE_RE = re.compile(r"喏|自己看|给你看|拿去看|看吧|给你瞧|发你|发给你|发过去|卡片?在这|给你卡")
CARD_REFUSE_RE = re.compile(r"不给|不发|不看|没发|不让看|不想给|不告诉|才不")
# card_ask 關掉、語義對上直接發的時候告訴她發了什麼（只講事實，說什麼她定）
CARD_SENT_HINT = "[系统提示，不要复述] TA 这句话像是想看〈我〉对 TA 的印象卡，程序已经直接发出去了："
REMEMBER_DAILY = 5  # 「记住」一個人一天最多當場記幾條（10-28 她自己選的 5；設定 remember_daily 可改），超過的不記
FRESH_N = 3  # 說話時帶這一天當場記的最多幾條（她選的）
CORRECT_PEOPLE = 6  # 記憶更正時最多整理幾個人的心裡的 TA（每人一次模型呼叫）
HEART_FIELDS = ("称呼", "印象", "喜好", "雷区", "梗", "最近", "语气", "牵挂", "私下", "回忆")


def system_tail(req, text: str):
    """每輪都一樣的說明接在 system 最後：排在對話歷史前面，DeepSeek 前綴快取命中得到（已經有了就不重複加）"""
    sp = getattr(req, "system_prompt", "") or ""
    if text and text not in sp:
        req.system_prompt = f"{sp.rstrip()}\n\n{text}" if sp.strip() else text


def _blank(v):
    """空的一欄（""、[]、{"牵挂": [], "心事": []}）都當成沒寫，比較心裡的 TA 動了哪幾欄時不算動"""
    if isinstance(v, dict):
        v = {k: _blank(x) for k, x in v.items() if _blank(x) is not None}
    return v or None


def _flat(s: str) -> str:
    """比一行是不是记住的那句：空白、標點都不算"""
    return re.sub(r"[\s，。,.！!？?～~、…「」\"'“”]", "", s or "")

HELP ="""好感指令（只有主人能在私聊用）
/好感 列表
/好感 查 QQ号
/好感 设 QQ号 好感|熟悉 数值（好感度 -80～80）
/好感 印象 QQ号 文字（写「清空」可清掉）
/好感 状态（〈我〉自己此刻的状态）
/好感 卡片例句（看学到的要印象卡说法） / 加 句子 / 不算 句子 / 删 编号
/好感 回忆 QQ号（和 TA 的片段） / 心里 QQ号（心里的 TA） / 感情 QQ号（各种感情和态度）
/好感 群梗 [群号] / 关系 QQ号（群梗、群友之间，只记在后台）
/好感 态度 QQ号 0～4（正常、冷淡、戒备、冷处理、封闭；写 0 等于原谅，补救机会重新算）
/好感 整理（现在就回味一遍新记忆）
/好感 重置 QQ号"""


def _fmt_time(ts: float) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d %H:%M")


def relayed_what(event: AstrMessageEvent) -> str:
    """這條消息帶了別人的東西（引用、轉發、分享卡片、不是表情包的圖）就返回「带了引用和截图」這種說法，沒有返回空字符串。
    讀 NapCat 原始消息；表情包（sub_type=1、mface）只是語氣，不算"""
    raw = getattr(event.message_obj, "raw_message", None)
    try:
        segs = raw.get("message") if raw is not None else None
    except Exception:
        segs = None
    if not isinstance(segs, list):
        return ""
    got = []
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        t, data = seg.get("type"), seg.get("data") or {}
        if t == "reply":
            got.append("引用")
        elif t in ("forward", "node"):
            got.append("转发的聊天记录")
        elif t == "json":
            got.append("分享的卡片")
        elif t == "image":
            sub = str(data.get("sub_type", data.get("subType", "")))
            if sub != "1" and "表情" not in str(data.get("summary", "")):
                got.append("截图或图片")
    got = list(dict.fromkeys(got))
    return ("带了" + "和".join(got)) if got else ""


BEAT_EXTRA = "lively_beat"  # 自主插件做的內部消息（〈我〉看手機時想說話，交給聊天這邊說）

@register(
    "astrbot_plugin_lively_bond",
    "GUCine",
    "情感系統：熟悉度、好感、心情、印象；每晚從記憶裡回味和每個人的片段，算出各種感情、整理「心裡的 TA」、記下態度；群友要「印象卡」時發一張印象卡圖片",
    "2.2.0",
)
class LivelyBond(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.s = Settings(context, config)
        self._sync_owners()
        data_dir = StarTools.get_data_dir("astrbot_plugin_lively_bond")
        self.store = BondStore(str(data_dir / "bond.db"))
        self.card_bg_dir = Path(data_dir) / "card_bg"  # 專屬〈卡〉背景：<QQ>.webp|png|jpg（＋<QQ>.txt 一句說明）
        if not self.store.get_meta(MODE_KEY):  # 10-26 拿掉了「/好感 模式」：新裝的直接啟用（以前先只記錄、主人再切）
            self.store.set_meta(MODE_KEY, "active")
        self.data = PLUGIN_DATA
        self._card_lock = asyncio.Lock()
        self._card_busy: set[tuple] = set()  # (qq, 聊天)：這張正在畫、正在發
        self._shared_umo: dict[str, list[float]] = {}
        self.intent = CardIntent(context, self.s, self.store)
        self.sorry = SorryIntent(context, self.s)
        self.heart = HeartStore(self.store.db)
        self.group = GroupNotes(self.store.db)
        self.digest = Digest(self.s, self.store, self.heart, PLUGIN_DATA)
        self.rel = Relations(self.store, self.heart, feed_tiers=lambda: (
            self.s.get("feed_tiers", FEED_TIERS) if self.s.get("feed_card", True) else ""))  # 關掉就不分檔
        self.digest.rel = self.rel  # 整理心裡的 TA 時記〈我〉的偏心
        self.digest.mem = self.rel.mem = self._memory  # 回味讀記憶插件切好的聊天、關係帶圖上的線（11-01）
        self.asks = OwnerAsks(self.store.db)  # 〈主人〉交代的事（asks.py 上面有來由）
        self.rel.asks = self.asks
        self.rel.cursed = self._cursed
        self.digest.is_enemy = self.rel.is_enemy
        self.self_notes = SelfNotes(self.store.db)  # 〈我〉自己的事（self_notes.py 上面有來由）
        self.digest.group = self.group  # 群梗、群友之間（只記後台）
        self._eased_at = 0.0
        if self.digest.upgrade():
            logger.info("[lively_bond] 回味的判断规则改了：清掉以前回味的结果，从人物卡重新搬、记忆重新回味")
        if (n := self.digest.requeue_singles()):
            logger.info(f"[lively_bond] 以前当成摘要跳过的 {n} 条单条记忆放回去重新回味")
        self._seed_hearts()
        self._retire_cards()
        self._task = asyncio.create_task(self._card_loop())
        logger.info(f"[lively_bond] 已加载，模式: {self.store.get_meta(MODE_KEY)}")

    async def terminate(self):
        self._task.cancel()

    # ---------- 關係讀口（給其他插件用；回傳的說明文字帶〈我〉〈主人〉佔位符，要自己 fill） ----------
    def _memory(self):
        """記憶插件（lively_memory），沒載入回 None"""
        meta = self.context.get_registered_star("astrbot_plugin_lively_memory")
        return meta.star_cls if meta and getattr(meta, "star_cls", None) and getattr(meta, "activated", True) else None

    def _cursed(self, qq):
        """〈主人〉诅咒的人（诅咒插件）；它沒載入回 None"""
        meta = self.context.get_registered_star("astrbot_plugin_lively_curse")
        c = meta.star_cls if meta and getattr(meta, "star_cls", None) and getattr(meta, "activated", True) else None
        try:
            return c.cursed(qq) if c and hasattr(c, "cursed") else None
        except Exception:
            return None

    def feel_prompt(self, group: bool = False) -> tuple[str, str]:
        """記憶插件抽記憶點時順便照這裡的規則記片段（情感記憶合一第 1 步，11-02）：(人設＋主人規矩, 片段規則)"""
        return self.digest.feel_prompt(group)

    def feel_live(self, people: dict, ts: float) -> str:
        """這些人那天當場記過的（「记住」），給記憶插件抽片段時避開"""
        return self.digest.feel_live(people, ts)

    def heart_change(self, qq: str, field: str, action: str, value: str, private: bool = False, why: str = "",
                     source: str = "memory") -> tuple[bool, str]:
        """別的插件改心裡的 TA 的一欄，跟 lively_heart_edit 同一套（記憶插件：她或〈主人〉改了從這裡抄過去的那條，11-02）。
        回 (改成了沒有, 說明)；沒建過頁的人不建"""
        qq = str(qq or "").strip()
        if not qq.isdigit() or (self.store.get(qq) is None and qq not in core.OWNERS):
            return False, "小本本上没有这个号"
        ok, msg = self.heart.edit(qq, str(field or ""), str(action or ""), str(value or "").strip()[:120], private=private)
        if ok:
            self.store.log(qq, "heart_edit", 0, 0, (msg + (f"｜{why}" if why else ""))[:200], source)
            self.store.db.commit()
        return ok, self.s.fill(msg)

    def relation(self, qq) -> dict:
        self._sync_owners()
        return self.rel.of(str(qq))

    def relations(self) -> list[dict]:
        self._sync_owners()
        return self.rel.all()

    def card_bgs(self) -> dict:
        """專屬〈卡〉背景：qq -> {path, v, dark, deep, look}（look 帶〈我〉〈主人〉佔位符）。面板也用它"""
        return card_img.fx.own_bgs(self.card_bg_dir)

    def made_avatars(self) -> dict:
        """群友定做的虛擬形象（comic 的 lively_avatar）：qq -> {card, sheet, v}。〈卡〉上放它，不放 QQ 頭像；
        主人照舊用 QQ 頭像（10-27 用戶定）。面板也用它"""
        return {q: r for q, r in card_img.made_avatars(PLUGIN_DATA / "astrbot_plugin_lively_comic" / "avatars",
                                                     PLUGIN_DATA / "astrbot_plugin_lively_comic" / "avatars_meta").items()
                if not self.s.is_owner(q)}

    def is_bot(self, qq) -> bool:
        return self.rel.is_bot(qq)

    def set_bot(self, qq, on: bool, why: str = "", by: str = "", vendor: str | None = None, model: str | None = None) -> bool:
        """標 TA 是不是 bot、哪家做的（诅咒插件、面板用）"""
        return self.rel.set_bot(qq, on, why, by, vendor, model)

    def bot_info(self, qq) -> dict | None:
        return self.rel.bot_info(qq)

    @filter.llm_tool("lively_bot")
    async def bot_note(self, event: AstrMessageEvent, action: str = "", qq: str = "", vendor: str = "", model: str = "", how: str = ""):
        """记下一个号是 bot（程序在说话，不是真人）、是哪家的模型做的。看出来了、TA 自己说了、别人告诉你的，你也认同时都可以记；
        记下以后你跟 TA 说话、别人提到 TA 时会带上「TA 是个 bot（哪家做的）」，面板、印象卡上 TA 的名字后面会画那家的标志。
        〈主人〉标过的（在后台或诅咒名单上）你改不了，回传会照实说；在和〈主人〉的私聊里记的算〈主人〉定的。
        对哪家怎么看是你自己的事，想记可以用 lively_self_note。

        Args:
            action(string): 记（标成 bot，或改哪家、型号）、看、不是（拿掉 bot 标记）
            qq(string): 谁，QQ 号；不写就是正在说话的这个人
            vendor(string): 哪家做的：DeepSeek、OpenAI/ChatGPT、Claude、Gemini、通义千问、豆包、Kimi、智谱/GLM，别的写「别家」，不知道就不写
            model(string): 型号，知道就写，比如 GPT-4o
            how(string): 怎么知道的，一句话（TA 自己说的、看出来的、谁告诉的）
        """
        self._sync_owners()
        speaker, _ = self._who(event)
        target = str(qq or "").strip() or speaker or ""
        if not target.isdigit():
            return "没记：不知道是谁（qq 写 QQ 号）"
        if target in core.OWNERS:
            return "没记：〈主人〉不是 bot"
        owner_dm = not event.get_group_id() and self.s.is_owner(speaker)
        act = str(action or "记").strip()
        info = self.rel.bot_info(target)
        name = (self.store.get(target) or {}).get("name") or target
        if act in ("看", "查"):
            return self.s.fill(f"「{name}」：" + (bot_text(info) if info else "没标过是 bot"))
        if info and info["owner_set"] and not owner_dm:
            return self.s.fill(f"没改：「{name}」是不是 bot、哪家做的是〈主人〉标的，〈我〉改不了；觉得不对可以私信〈主人〉说")
        by = "owner" if owner_dm else "her"
        if act in ("不是", "删", "拿掉"):
            changed = self.rel.set_bot(target, False)
            return self.s.fill(f"「{name}」不再标成 bot 了" if changed else f"「{name}」本来就没标成 bot")
        v = vendor_key(vendor) if vendor else None
        changed = self.rel.set_bot(target, True, "〈主人〉说的" if owner_dm else "〈我〉记的", by, v,
                                   model if model else None, how if how else None)
        self.store.log(target, "bot", 0, 0, f"{'记' if changed else '没变'}：{vendor or '-'} {model or ''} {how or ''}"[:200], by)
        self.store.db.commit()
        return self.s.fill((f"记下了：" if changed else "本来就是这样记的：") + f"「{name}」" + bot_text(self.rel.bot_info(target)).replace("TA ", "", 1))

    def opening(self, qq) -> dict:
        """要私聊 TA：接話（continuing）不擋；開新話題看 ok/why（說明帶〈我〉，要自己 fill）"""
        self._sync_owners()
        return self.rel.opening(str(qq))

    def note_reach(self, qq, by: str):
        self.rel.note_reach(str(qq), by)

    def reaches_today(self) -> int:
        return self.rel.reaches_today()

    def quiet_people(self) -> dict:
        return self.rel.quiet_people()

    def set_quiet(self, qq, off: bool = False, name: str = "", why: str = "", ts: float | None = None) -> bool:
        return self.rel.set_quiet(str(qq), off, name, why, ts)

    def memory_moments(self, doc_id) -> list[dict]:
        """某條長期記憶回味出的片段（mind 的記憶自查用）"""
        return self.heart.moments_by_ref(str(doc_id))

    def forget_memory(self, doc_id) -> list[dict]:
        """某條長期記憶被忘掉了，片段跟著刪；回傳刪掉的"""
        return self.heart.forget_ref(str(doc_id))

    async def correct_memory(self, old_ref: str = "", new_ref: str = "", people=(), note: str = "") -> dict:
        """一條長期記憶被〈主人〉確認更正或忘掉了（mind 的記憶自查叫）：
        old_ref 那條回味出的片段刪掉；new_ref（改過的那條；原地改的和 old_ref 一樣）照改過的內容當場重新回味；
        從那條長出片段的人、people 裡寫的人，心裡的 TA 帶著更正 note 當場整理一次（不等晚上），印象跟著換。
        回 {"moments_removed", "moments_new", "hearts": [{"qq","name","fixes","changed"}], "failed": [qq…]}；
        changed 是心裡的 TA 實際動了哪幾欄（模型寫的「改了」偶爾會說改了其實沒有的東西，回報以 changed 為準）"""
        self._sync_owners()
        old_ref, new_ref = str(old_ref or ""), str(new_ref or "")
        bot = self.s.bot_qq()
        out = {"moments_removed": 0, "moments_new": 0, "hearts": [], "failed": []}
        async with self._card_lock:  # 和每晚回味錯開，免得同一條讀兩次
            gone = self.heart.forget_ref(old_ref) if old_ref else []
            out["moments_removed"] = len(gone)
            who = []
            for q in [str(m.get("qq") or "") for m in gone] + [str(x) for x in people]:
                if q.isdigit() and q != bot and q not in who:
                    who.append(q)
            if new_ref:
                self.heart.undigest(new_ref)
                try:
                    _, moms, _ = await self.digest.digest(limit=3, only={new_ref})
                    out["moments_new"] = moms
                    for m in self.heart.moments_by_ref(new_ref):
                        q = str(m.get("qq") or "")
                        if q.isdigit() and q != bot and q not in who:
                            who.append(q)
                except Exception as e:
                    logger.warning(f"[lively_bond] 记忆更正后重新回味失败 {new_ref}: {e}")
            for qq in who[:CORRECT_PEOPLE]:
                if self.heart.get_heart(qq) is None:
                    continue
                old = self.heart.get_heart(qq) or {}
                try:
                    h = await self.digest.update_heart(qq, self.s.fill(note))
                except Exception as e:
                    logger.warning(f"[lively_bond] 记忆更正后整理心里的 TA 失败 {qq}: {e}")
                    out["failed"].append(qq)
                    continue
                p = self.store.get(qq)
                imp = str((h or {}).get("印象") or "").strip()
                if p and imp and imp != p.get("impression"):
                    self.store.db.execute("UPDATE people SET impression=? WHERE qq=?", (imp[:IMPRESSION_MAX], qq))
                    self.store.log(qq, "admin", 0, 0, f"记忆更正后印象换成:{imp[:IMPRESSION_MAX]}", "admin")
                    self.store.db.commit()
                changed = [k for k in HEART_FIELDS if _blank(old.get(k)) != _blank((h or {}).get(k))]
                out["hearts"].append({"qq": qq, "name": (p or {}).get("name") or qq,
                                      "fixes": list(self.digest.last_fixes) if changed else [], "changed": changed})
        logger.info(f"[lively_bond] 记忆更正：删了 {out['moments_removed']} 段片段，重新回味出 {out['moments_new']} 段，"
                    f"整理了 {len(out['hearts'])} 个人的心里的 TA")
        return out

    def _active(self) -> bool:
        return self.store.get_meta(MODE_KEY) == "active"

    @staticmethod
    def _sender(event: AstrMessageEvent) -> dict:
        """NapCat 原始消息裡的發送人（nickname 是 QQ 暱稱，群消息另有 card 群名片）"""
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        try:
            s = raw.get("sender") if raw is not None else None
        except Exception:
            s = None
        return s if isinstance(s, dict) else {}

    @classmethod
    def _who(cls, event: AstrMessageEvent):
        """(QQ, 名字)。名字用 QQ 暱稱（10-27 起）：AstrBot 給的名字是群名片優先，同一個人在不同群名字不一樣，
        以前每條消息都拿它蓋名字，主人在哪個群說話後台就變成哪個群的群名片。拿不到暱稱才用 AstrBot 給的"""
        qq = str(event.get_sender_id() or "").strip()
        if not qq or qq == str(event.get_self_id() or ""):
            return None, ""
        nick = str(cls._sender(event).get("nickname") or "").strip()
        return qq, nick or (event.get_sender_name() or "").strip()

    @classmethod
    def _card(cls, event: AstrMessageEvent) -> str | None:
        """群消息的群名片（沒設是空字串）；不是群消息、原始消息裡沒有這一欄返回 None"""
        if not event.get_group_id():
            return None
        s = cls._sender(event)
        return str(s.get("card") or "").strip() if "card" in s else None

    @staticmethod
    def _source(event: AstrMessageEvent) -> str:
        return "group" if event.get_group_id() else "dm"

    def _sync_owners(self):
        """主人的 QQ 在門禁插件的設定裡；bond 比門禁先載入，開機那一刻讀不到，所以之後每次用到前再對一遍"""
        ids = set(self.s.owners())
        if ids != core.OWNERS:
            core.OWNERS.clear()
            core.OWNERS.update(ids)

    # 看到消息：更新上次見面、熟悉度、好久不見（閘門插件優先順序 1000，已先過濾別的群和機器人）
    @filter.event_message_type(filter.EventMessageType.ALL, priority=999)
    async def seen(self, event: AstrMessageEvent):
        self._sync_owners()
        gid = str(event.get_group_id() or "")
        if gid and gid not in self.s.groups():
            return
        self.s.remember_self(event)
        qq, name = self._who(event)
        if not qq or event.get_extra(BEAT_EXTRA):  # 自主插件做的內部消息（〈我〉看手機時想說話）：TA 沒說話，不算見面
            return
        try:
            self.store.on_seen(qq, name)
            card = self._card(event)
            if card is not None:
                self.store.note_card(qq, gid, card)
        except Exception as e:
            logger.error(f"[lively_bond] 记录见面失败: {e}")

    # 最後追加（排在記憶、圖片提示之後），離模型開始寫回復最近，模型更不容易漏寫標記
    @filter.on_llm_request(priority=-10)
    async def inject(self, event: AstrMessageEvent, req):
        qq, name = self._who(event)
        if not qq:
            return
        beat = bool(event.get_extra(BEAT_EXTRA))
        try:
            # 〈我〉看手機時想說話交給聊天這邊的：TA 沒在說話，不算一次聊天（熟悉度、聊過幾次都不動），也不看像不像道歉
            p = self.store.load(qq) if beat else self.store.on_talk(qq, name)
            heart, moms = self._heart_bits(event, qq, self._active() and self.s.get("inject_heart", False))
            sorry = "" if beat else await self._sorry_line(event, qq)
            note = build_prompt(p, self._active(), me=self.store.self_state(),
                                heart=heart, moments=moms, relayed=relayed_what(event), rel=self.rel.say(self.rel.of(qq)),
                                memories=self._memories_line(event, qq), alias=self._alias(event, p),
                                fresh=self._fresh_lines(event, qq), sorry=sorry, remember=self._remember_on())
            req.extra_user_content_parts.append(TextPart(text=self.s.fill(note)).mark_as_temp())
            event.set_extra("lively_bond_qq", qq)
            event.set_extra("lively_bond_subj", self._narrate_subjects(event, qq, p))
        except Exception as e:
            logger.error(f"[lively_bond] 注入失败: {e}")
        try:
            told = self._asks_text(event, qq, name)
            if told:
                req.extra_user_content_parts.append(TextPart(text=self.s.fill(told)).mark_as_temp())
        except Exception as e:
            logger.warning(f"[lively_bond] 读主人交代的事失败: {e}")
        try:
            # 〈我〉自己的事不常變：接在 system 最後（排在起草提醒、相處經驗後面），前綴快取接得上
            mine = self.self_notes_text()
            if mine:
                system_tail(req, self.s.fill(mine))
        except Exception as e:
            logger.warning(f"[lively_bond] 读自己的事失败: {e}")
        if self._active() and self.s.get("mention_people", True):
            try:
                about = self.mentioned_text(event, qq)
                if about:
                    req.extra_user_content_parts.append(TextPart(text=self.s.fill(about)).mark_as_temp())
            except Exception as e:
                logger.warning(f"[lively_bond] 读提到的人失败: {e}")
        if beat or not await self._asks_card(event):
            return
        if self.s.get("card_ask", True):
            event.set_extra("lively_card_hinted", True)
            req.extra_user_content_parts.append(TextPart(text=self.s.fill(CARD_ASK_HINT)).mark_as_temp())
            logger.info(f"[lively_bond] {qq} 像在要印象卡，提醒她自己定发不发")
        else:
            ok, msg = await self._send_card(event)
            event.set_extra("lively_card_done", msg)
            hint = (CARD_SENT_HINT + msg + "这条消息再调用 lively_impression_card 也不会再发一张" if ok
                    else f"[系统提示，不要复述] TA 想看〈我〉对 TA 的印象卡。{msg}")
            req.extra_user_content_parts.append(TextPart(text=self.s.fill(hint)).mark_as_temp())

    def _asks_text(self, event: AstrMessageEvent, qq: str, name: str) -> str:
        """〈主人〉交代的事：正在說話的人身上的、這個群的、到哪都算的（話裡提到的人在 say_about 裡帶）"""
        gid = str(event.get_group_id() or "")
        out = asks_block(self.asks.for_person(qq), name, self.asks.for_group(gid) if gid else [], self.asks.everywhere())
        if self.s.is_owner(qq) and (n := len(self.asks.all())):
            out = (out + "\n" if out else "") + f"（〈主人〉交代过的一共 {n} 条，lively_owner_rule 看得到全部）"
        return out

    @filter.on_llm_request(priority=100)
    async def hide_owner_rule(self, event: AstrMessageEvent, req):
        """交代的本子只有〈主人〉說話的聊天裡看得到：別人在的聊天裡翻出來就是把〈主人〉私下說的話攤開"""
        self._sync_owners()
        if self.s.is_owner(event.get_sender_id()):
            return
        tools = getattr(req, "func_tool", None)
        if tools is not None and hasattr(tools, "remove_tool"):
            tools.remove_tool("lively_owner_rule")

    def _ask_targets(self, event: AstrMessageEvent, who: str, group: str) -> tuple[list[str], str]:
        """who、group 寫的換成 qq:號 / group:號；都沒寫是到哪都算。認不出的回原因"""
        out = []
        for w in re.split(r"[\s,，、;；]+", str(who or "").strip()):
            if not w:
                continue
            if w.isdigit() and 5 <= len(w) <= 12:
                out.append(f"qq:{w}")
                continue
            rows = self.store.db.execute("SELECT qq FROM people WHERE name=?", (w,)).fetchall() or \
                self.store.db.execute("SELECT qq FROM people WHERE name LIKE ?", (f"%{w}%",)).fetchall()
            if len(rows) != 1:
                return [], (f"认不出「{w}」是谁：" + ("小本本上没有叫这个名字的人" if not rows else f"对得上 {len(rows)} 个人")
                            + "，who 写 QQ 号")
            out.append(f"qq:{rows[0][0]}")
        for g in re.split(r"[\s,，、;；]+", str(group or "").strip()):
            if not g:
                continue
            if g in ("这个群", "这里", "本群") and event.get_group_id():
                g = str(event.get_group_id())
            if not g.isdigit():
                return [], f"认不出群「{g}」，group 写群号（在群里可以写「这个群」）"
            out.append(f"group:{g}")
        return out or [ALL], ""

    def _ask_where(self, a: dict) -> str:
        def name_of(q):
            if self.s.is_owner(q):
                return "〈主人〉"
            p = self.store.get(q)
            return (p or {}).get("name") or "〈我〉还不认识这个号"

        where = where_text(a, name_of, lambda g: "" if g in self.s.groups() else "〈我〉不在这个群说话")
        if a["targets"] == [ALL]:
            return f"{where}：以后每一轮说话〈我〉都会看到这条"
        return (f"挂在 {where} 身上：以后 TA 跟〈我〉说话、被 @、被提到（写了号码）时，在挂着的群里说话时，〈我〉都会看到这条")

    @filter.llm_tool("lively_owner_rule")
    @staticmethod
    def _ask_once(a: dict) -> str:
        """記、改的這條寫了「今天」「明天」這種說哪一天的：照實說到什麼時候就不再帶（11-02）"""
        if not a.get("until"):
            return ""
        return (f"。这条写了说哪一天的词（今天、明天这种），算那天的事，{until_note(a)}；"
                "要一直记着就改成不带这种词的说法")

    async def owner_rule(self, event: AstrMessageEvent, action: str = "", text: str = "", who: str = "", group: str = "", id: str = "",
                         why: str = ""):
        """〈主人〉交代你的事，记在一个随身带着的本子上：记下的以后到别的聊天也会带着（挂在某个人身上的，TA 跟你说话、被 @、被提到号码时带；
        挂在某个群上的，在那个群说话时带；都不挂就是到哪都算，每一轮都带，自己隔一阵看 QQ 的时候也带）。聊天里说过的话，过后自己看 QQ 时不一定看得到，要那时也记得就记在这里。只有〈主人〉在说话的聊天里有这个工具，别人看不到这个本子。
        〈主人〉交代、你也愿意记着的时候可以用；写的是你自己的话。
        〈主人〉说的仇人、要你针对的人不记在这里，记在诅咒名单上（lively_curse）：这个本子上挂在人身上的，那人被别人提到时会带出原话。

        Args:
            action(string): 记、看、改、删
            text(string): 交代的是什么，你自己的话，一条最多 120 字（记、改的时候写）；写了「今天」「明天」这种说哪一天的，就算那天的事，那天过完就不再带
            who(string): 挂在谁身上：QQ 号或名字，几个人用空格分开；不写就不挂人
            group(string): 挂在哪个群：群号，在群里可以写「这个群」；不写就不挂群。who、group 都不写就是到哪都算
            id(string): 改、删哪一条：交代的编号（看的时候列出来的数字）
            why(string): 为什么记、为什么改，一句话（可以不写）
        """
        # why 10-29 加：別的工具都有 why，她順手寫上時這個工具以前直接報錯（unexpected keyword argument 'why'）
        reason = str(why or "").strip()[:40]
        self._sync_owners()
        if not self.s.is_owner(event.get_sender_id()):
            return self.s.fill("没做：这个本子只有〈主人〉在说话的聊天里能动")
        act = str(action or "").strip()
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if act in ("看", "列", "查", ""):
            rows = self.asks.all()
            if not rows:
                return self.s.fill("本子上还没有〈主人〉交代的事")
            return self.s.fill(f"〈主人〉交代过的 {len(rows)} 条：\n" + "\n".join(
                f"交代{a['id']}（{datetime.fromtimestamp(a['ts'], TZ).strftime('%m-%d')}）{where_text(a)}：{a['text']}"
                + (f"（{until_note(a)}）" if a.get("until") else "") for a in rows))
        src = f"group:{event.get_group_id()}" if event.get_group_id() else "dm"
        if act in ("记", "加", "记下"):
            targets, why = self._ask_targets(event, who, group)
            if why:
                return self.s.fill("没记：" + why)
            why = self.asks.check(text, targets)
            if why:
                return self.s.fill("没记：" + why)
            a = self.asks.add(text, targets, src)
            self.store.log(self.s.owner() or "", "owner_ask", 0, 0, (f"记 交代{a['id']}｜{where_text(a)}｜{text}" + (f"｜{reason}" if reason else ""))[:200], self._source(event))
            self.store.db.commit()
            logger.info(f"[lively_bond] 记下主人交代的事 交代{a['id']}（{where_text(a)}）：{text}")
            return self.s.fill(f"记下了（交代{a['id']}）：「{text}」，{self._ask_where(a)}{self._ask_once(a)}")
        if act not in ("删", "删掉", "划掉", "改", "改成"):
            return self.s.fill("没做：action 写 记、看、改、删")
        a = self.asks.get(id)
        if not a:
            return self.s.fill(f"没找到交代「{id}」：用「看」列出编号")
        if act in ("删", "删掉", "划掉"):
            self.asks.drop(a["id"])
            self.store.log(self.s.owner() or "", "owner_ask", 0, 0, (f"删 交代{a['id']}｜{a['text']}" + (f"｜{reason}" if reason else ""))[:200], self._source(event))
            self.store.db.commit()
            logger.info(f"[lively_bond] 删了主人交代的事 交代{a['id']}：{a['text']}")
            return self.s.fill(f"删了交代{a['id']}：「{a['text']}」，以后不会再带着")
        if act in ("改", "改成"):
            targets = None
            if str(who or "").strip() or str(group or "").strip():
                targets, why = self._ask_targets(event, who, group)
                if why:
                    return self.s.fill("没改：" + why)
            new_text = text or a["text"]
            why = self.asks.check(new_text, targets or a["targets"], skip=a["id"])
            if why:
                return self.s.fill("没改：" + why)
            b = self.asks.edit(a["id"], new_text, targets)
            self.store.log(self.s.owner() or "", "owner_ask", 0, 0, (f"改 交代{a['id']}｜{where_text(b)}｜{new_text}" + (f"｜{reason}" if reason else ""))[:200], self._source(event))
            self.store.db.commit()
            logger.info(f"[lively_bond] 改了主人交代的事 交代{a['id']}：{new_text}")
            return self.s.fill(f"改好了（交代{b['id']}）：「{b['text']}」，{self._ask_where(b)}{self._ask_once(b)}")
        return self.s.fill("没做：action 写 记、看、改、删")

    def self_notes_text(self) -> str:
        """〈我〉自己的事，每一輪帶給她的那段（心跳那頁也用）"""
        self._sync_owners()
        return notes_block(self.self_notes.all(), self.s.is_owner)

    def _self_where(self, event: AstrMessageEvent) -> str:
        gid = str(event.get_group_id() or "")
        return f"group:{gid}" if gid else f"dm:{event.get_sender_id() or ''}"

    def _self_tell_owner(self, event: AstrMessageEvent, n: dict, verb: str):
        """不是〈主人〉說的：在〈主人〉私聊記一筆，〈我〉下次跟〈主人〉說話時看得到（提不提她定）"""
        owner = self.s.owner()
        if not owner or self.s.is_owner(event.get_sender_id()):
            return
        where = "群里" if event.get_group_id() else f"和{(self._who(event)[1] or '一个人')}的私聊里"
        told = told_text(n, self.s.is_owner)
        note_outbox(self.s.private_umo(owner), self.s.fill(
            f"〈我〉在{where}{verb}了一条自己的事：S{n['id']}「{n['text']}」" + (f"（{told}）" if told else "")
            + "。〈主人〉在后台看得到这本、能删"), kind="后台:自己的事")

    @filter.llm_tool("lively_self_note")
    async def self_note(self, event: AstrMessageEvent, action: str = "", text: str = "", id: str = "", who: str = "", why: str = ""):
        """你自己的事，记在一个随身带着的本子上：你住在哪、手上有什么、最近自己身上变了什么、自己的习惯（比如平时隔多久看一眼 QQ）这类关于你自己的事。
        记下的以后每一轮、每个聊天都带着（自己隔一阵看 QQ 的时候也带；聊天里说过的话那时不一定看得到），记错了、过时了可以改、删。跟某个人有关的事、要去做的事、〈主人〉的规矩不记在这里。
        谁说的都能记，记不记你判断；每条会记下是谁告诉你的，不是〈主人〉说的，〈主人〉那边会知道你记了什么。

        Args:
            action(string): 记、看、改、删
            text(string): 记的是什么，你自己的话，一条最多 150 字（记、改的时候写）
            id(string): 改、删哪一条：编号（看的时候列出来的，S 后面的数字）
            who(string): 这件事是谁告诉你的：不写就是正在跟你说话的人；你自己发现、自己知道的写「自己」
            why(string): 为什么记、为什么改，一句话（可以不写）
        """
        reason = str(why or "").strip()[:40]
        self._sync_owners()
        act = str(action or "").strip()
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        rows = self.self_notes.all()
        if act in ("看", "列", "查", ""):
            if not rows:
                return self.s.fill("本子上还没有记自己的事")
            return self.s.fill(f"本子上 {len(rows)} 条，一共 {self.self_notes.total()} 个字（最多 {SELF_MAX} 个字）：\n"
                               + "\n".join(note_line(n, self.s.is_owner) for n in rows))
        if act in ("记", "加", "记下"):
            why = self.self_notes.check(text)
            if why:
                return self.s.fill("没记：" + why)
            w = str(who or "").strip()
            if w in ("自己", "我", "本鱼", "〈我〉", "self"):
                by, name = SELF, ""
            else:
                by, name = self._who(event)
                if w and w not in (name, by):
                    name = w[:20]
            n = self.self_notes.add(text, self._self_where(event), by, name)
            self.store.log(self.s.owner() or "", "self_note", 0, 0, (f"记 S{n['id']}｜{text}" + (f"｜{reason}" if reason else ""))[:200], self._source(event))
            self.store.db.commit()
            logger.info(f"[lively_bond] 记下自己的事 S{n['id']}（{by or '?'}）：{text}")
            self._self_tell_owner(event, n, "记")
            return self.s.fill(f"记下了：{note_line(n, self.s.is_owner)}。以后每一轮都会带着；"
                               f"本子现在 {len(rows) + 1} 条、{self.self_notes.total()} 个字")
        if act not in ("删", "删掉", "划掉", "改", "改成"):
            return self.s.fill("没做：action 写 记、看、改、删")
        n = self.self_notes.get(id)
        if not n:
            return self.s.fill(f"没找到「{id}」：用「看」列出编号")
        if act in ("删", "删掉", "划掉"):
            self.self_notes.drop(n["id"])
            self.store.log(self.s.owner() or "", "self_note", 0, 0, (f"删 S{n['id']}｜{n['text']}" + (f"｜{reason}" if reason else ""))[:200], self._source(event))
            self.store.db.commit()
            logger.info(f"[lively_bond] 删了自己的事 S{n['id']}：{n['text']}")
            return self.s.fill(f"删了 S{n['id']}：「{n['text']}」，以后不会再带着")
        why = self.self_notes.check(text, skip=n["id"])
        if why:
            return self.s.fill("没改：" + why)
        b = self.self_notes.edit(n["id"], text)
        self.store.log(self.s.owner() or "", "self_note", 0, 0, (f"改 S{n['id']}｜{n['text']} → {text}" + (f"｜{reason}" if reason else ""))[:200], self._source(event))
        self.store.db.commit()
        logger.info(f"[lively_bond] 改了自己的事 S{n['id']}：{n['text']} → {text}")
        self._self_tell_owner(event, b, "改")
        return self.s.fill(f"改好了：{note_line(b, self.s.is_owner)}（原来是「{n['text']}」）")

    def _remember_on(self) -> bool:
        return bool(self.s.get("remember", True))

    def _fresh_lines(self, event: AstrMessageEvent, qq: str) -> list[str]:
        """這一天當場記下的、和 TA 的事（她 10-28 選的：最多 3 條）。私聊的在群裡也帶，標明是私聊裡的（和專屬回憶一樣，提不提她判斷）"""
        if not self._remember_on():
            return []
        try:
            group = bool(event.get_group_id())
            return [moment_line(m, hide=group)
                    for m in self.heart.fresh(qq, n=FRESH_N)]
        except Exception as e:
            logger.warning(f"[lively_bond] 读当场记下的事失败: {e}")
            return []

    async def _sorry_line(self, event: AstrMessageEvent, qq: str) -> str:
        """〈我〉對 TA 現在不是正常態度、TA 這條像在道歉（bge-m3 篩）：照實告訴〈我〉，原不原諒她在標記裡寫 |原谅（10-28 用戶定「由她決定」）。
        不判誠不誠懇、不替她降級"""
        if not self.s.get("sorry_live", True) or qq in core.OWNERS:
            return ""
        st = self.heart.get_stance(qq)
        att = int(st["attitude"] or 0)
        if att <= 0:
            return ""
        try:
            hit, sy, _ = await self.sorry.check(event.message_str or "")
        except Exception as e:
            logger.warning(f"[lively_bond] 道歉语义判断失败: {e}")
            return ""
        if not hit:
            return ""
        after = self.heart.forgive_preview(qq)
        why = self.rel.of(qq)["attitude_reason"][:40]
        line = (f"TA 这条像是在道歉。〈我〉现在对 TA 是{ATTITUDES[att]}"
                + (f"（因为 {gday(st['changed'])}：{why}）" if why else "") + "。原不原谅由〈我〉自己定：")
        if after < att:
            line += f"原谅就在最后的标记里加一段 |原谅，〈我〉对 TA 的态度会缓一级，变成{ATTITUDES[after]}；不原谅就照常写，态度不变"
        elif st["offenses"] >= 3 and att <= 1:
            line += (f"TA 已经惹过〈我〉{st['offenses']} 次了，〈我〉自己最多只能缓到冷淡，再往下要〈主人〉；"
                     "原谅也可以在标记里加 |原谅，记下来，但态度不变")
        else:
            line += "TA 这一阵已经道过歉、〈我〉也定过了，这次就算原谅态度也不会再变；想记下来照样可以加 |原谅"
        event.set_extra("lively_bond_sorry", {"att": att, "text": (event.message_str or "")[:40], "sim": round(sy, 2)})
        logger.info(f"[lively_bond] {qq} 像在道歉（{sy:.2f}），告诉她，原不原谅她定")
        return line

    def mentioned(self, event: AstrMessageEvent, speaker: str) -> list[str]:
        """這句話裡提到的人：@ 到的、單獨寫出來的號碼（只算認識的人、主人、服務的群；不認識的號要 6 位以上）。
        說話的人自己、〈我〉自己不算，最多 mention_max 個。不認名字：群名片太短、撞名多，容易對錯人"""
        skip = {str(speaker or ""), str(event.get_self_id() or ""), "all"}
        out: list[str] = []
        for c in getattr(event.message_obj, "message", None) or []:
            if isinstance(c, At):
                q = str(getattr(c, "qq", "") or "")
                if q.isdigit() and q not in skip and q not in out:
                    out.append(q)
        groups = set(self.s.groups())
        for m in MENTION_RE.finditer(event.message_str or ""):
            q = m.group(0)
            if q in skip or q in out:
                continue
            if q in groups or q in core.OWNERS or self.store.get(q) is not None or len(q) >= 6:
                out.append(q)
        return out[:max(0, int(self.s.get("mention_max", 3) or 0))]

    def mentioned_text(self, event: AstrMessageEvent, speaker: str) -> str:
        """10-26 主人私聊說「<某人的 QQ 號>给你投喂了15元」，她只拿到主人的資料，不知道那是泠·月（聊過 131 次），回了「不熟」"""
        lines = []
        groups = set(self.s.groups())
        for q in self.mentioned(event, speaker):
            if q in groups and q not in core.OWNERS and self.store.get(q) is None:
                lines.append(f"{q} 是〈我〉在的一个群的群号")
            else:
                lines.append(self.rel.say_about(self.rel.of(q)))
        if not lines:
            return ""
        return ("[系统提示，不要复述] 这句话里提到的人（不是正在说话的这位）：\n" + "\n".join(f"- {x}" for x in lines)
                + "\n（关于这些人的事要记在 TA 本人身上，可以用 lively_heart_edit，qq 写 TA 的号；|记住 记的是正在说话的这位）")

    def _heart_bits(self, event: AstrMessageEvent, qq: str, moments: bool = False) -> tuple[str, list[str]]:
        """心裡的 TA（私聊才帶「私下」那欄），moments 開著再帶一兩段跟這句話沾邊的片段。私聊的片段在群裡也帶，標明是私聊裡的，能不能接由她判斷"""
        dm = not event.get_group_id()
        try:
            heart = heart_text(self.heart.get_heart(qq), private=dm, memories=False, budget=core.HEART_MAX)
            n = int(self.s.get("heart_moments", 2) or 0) if moments else 0
            moms = pick_moments(self.heart.moments(qq, PICK_POOL), event.message_str or "", n) if n > 0 else []
            return heart, [moment_line(m) for m in moms]
        except Exception as e:
            logger.warning(f"[lively_bond] 读心里的 TA 失败: {e}")
            return "", []

    def _alias(self, event: AstrMessageEvent, p: dict) -> str:
        """群裡說話時，TA 在這個群的群名片和 QQ 暱稱不一樣就給她（她對得上群聊記錄裡的名字）"""
        card = self._card(event)
        if card is None and event.get_group_id():
            card = self.store.card_in(p["qq"], str(event.get_group_id()))
        return card if card and card != (p.get("name") or "") else ""

    def _memories_line(self, event: AstrMessageEvent, qq: str) -> str:
        """〈我〉和正在說話的 TA 的專屬回憶（heart.MEMORY_MAX 上面有來由）：私聊全部；群裡也全部，私聊裡的標出來"""
        if not self.s.get("heart_memories", True):
            return ""
        try:
            return memories_text(self.heart.get_heart(qq), "group" if event.get_group_id() else "dm")
        except Exception as e:
            logger.warning(f"[lively_bond] 读专属回忆失败: {e}")
            return ""

    async def _asks_card(self, event: AstrMessageEvent) -> bool:
        if not self.s.get("card_auto", True):
            return False
        # @ 了別人：在問別人的事，只能看自己的卡
        self_id = str(event.get_self_id())
        if any(isinstance(c, At) and str(c.qq) not in (self_id, "all") for c in event.get_messages()):
            return False
        try:
            return (await self.intent.check(event.message_str or ""))[0]
        except Exception as e:
            logger.warning(f"[lively_bond] 印象卡语义判断失败: {e}")
            return False

    def _narrate_subjects(self, event: AstrMessageEvent, qq: str, p: dict) -> list[str]:
        """回覆最後一行像记住時（core.narrated_tail）開頭會是誰：〈主人〉的稱呼、TA 的暱稱、群名片、〈我〉叫 TA 的、他／她／TA"""
        out = [self.s.fill("〈主人〉"), "他", "她", "TA", p.get("name") or ""]
        try:
            out.append(str((self.heart.get_heart(qq) or {}).get("称呼") or ""))
        except Exception:
            pass
        if event.get_group_id():
            out.append(self.store.card_in(qq, str(event.get_group_id())) or "")
        return [x for x in out if x and len(x) >= (1 if x in ("他", "她") else 2)]

    def _take_narrated(self, event: AstrMessageEvent, text: str, tag: dict | None) -> str:
        """回覆最後一行是她寫給自己的记住（連標記都沒寫）：從要發的話裡拿掉；這一輪還沒记住別的就當成记住記下"""
        subj = event.get_extra("lively_bond_subj")
        qq = event.get_extra("lively_bond_qq")
        if not subj or not qq:
            return text
        clean, line = narrated_tail(text, subj, self.s.me())
        if not line:
            return text
        event.set_extra("lively_bond_narrated", [*(event.get_extra("lively_bond_narrated") or []), _flat(line)])
        logger.info(f"[lively_bond] 回复最后一行像是写给自己的记住，没发出去：{line}")
        if tag is not None:
            tag["remember"] = tag.get("remember") or clean_remember(line)
        elif not event.get_extra("lively_bond_remember"):
            r = clean_remember(line)
            if r:
                event.set_extra("lively_bond_remember", r)
                try:
                    self._remember(event, str(qq), {"remember": r})
                except Exception as e:
                    logger.error(f"[lively_bond] 记住没记成: {e}")
        return clean

    def _note_remember_lines(self, event: AstrMessageEvent, lines: list[str], tag: dict | None):
        """回覆裡單獨寫成一行的「记住：…」「记住（…）」（core.REMEMBER_LINE_RE，11-02）已經從要發的話裡拿掉了：
        有標記就當標記裡的记住（parse_tag 已填），沒標記、這一輪還沒记住別的就直接記（不動好感、熟悉度）"""
        if not lines:
            return
        logger.info(f"[lively_bond] 回复里有「记住」那一行，没发出去：{lines[0]}")
        qq = event.get_extra("lively_bond_qq")
        if tag is not None:
            tag["remember"] = tag.get("remember") or lines[0]
        elif qq and not event.get_extra("lively_bond_remember"):
            event.set_extra("lively_bond_remember", lines[0])
            try:
                self._remember(event, str(qq), {"remember": lines[0]})
            except Exception as e:
                logger.error(f"[lively_bond] 记住没记成: {e}")

    # 在表情包、記憶插件之前取出標記，保證它們拿到的是乾淨文本
    @filter.on_llm_response(priority=100001)
    async def take_tag(self, event: AstrMessageEvent, resp):
        if not resp or not getattr(resp, "completion_text", None):
            return
        lines = remember_lines_of(resp.completion_text)
        clean, tag = parse_tag(resp.completion_text)
        self._note_remember_lines(event, lines, tag)
        clean = self._take_narrated(event, clean, tag)
        event.set_extra("lively_bond_narr_seen", _flat(clean))  # 發送前不再看一遍（不然拿掉一行後倒數第二行又被看一次）
        if clean == resp.completion_text and tag is None:
            return
        resp.completion_text = clean
        self._apply_tag(event, tag)

    def _apply_tag(self, event: AstrMessageEvent, tag: dict | None):
        """記好感、心情、记住、原谅。一輪只記一次：調工具之前那步帶的標記不經過 on_llm_response，
        要在發送前（last_check）記；之後最後那步再寫一個就不重複算（10-29 15:09 群：好感、记住都沒記上）"""
        qq = event.get_extra("lively_bond_qq")
        if not qq or tag is None or event.get_extra("lively_bond_tagged"):
            return
        event.set_extra("lively_bond_tagged", True)
        if tag.get("remember"):
            event.set_extra("lively_bond_remember", tag["remember"])
        if event.get_extra(BEAT_EXTRA) and tag.get("delta"):
            tag = dict(tag, delta=0)  # 〈我〉看手機時自己想說的話：TA 這輪沒說什麼，好感不加減（心情、记住照記）
        try:
            _, name = self._who(event)
            p = self.store.apply_tag(qq, name, tag, self._source(event), inertia=str(self.s.get("mood_inertia", "log") or "off"))
            if tag["impression"] and not self._remember_on():  # 10-28 起印象只在凌晨回味時改（她自己選的），「记住」關掉才照舊
                self.heart.note_impression(qq, tag["impression"])
            logger.info(
                f"[lively_bond] {name}({qq}) 好感{tag['delta']:+d} 心情:{tag.get('mood_word') or tag['mood'] or '-'}"
                f"{' 印象:' + tag['impression'] if tag['impression'] else ''} → {stage(p)}"
            )
        except Exception as e:
            logger.error(f"[lively_bond] 记录好感失败: {e}")
        try:
            self._remember(event, qq, tag)
            self._decide_sorry(event, qq, tag)
        except Exception as e:
            logger.error(f"[lively_bond] 记住／原谅没记成: {e}")

    def _remember(self, event: AstrMessageEvent, qq: str, tag: dict):
        """「记住」當場記一段片段（source=live）：只記事、不帶感情分數，免得晚上回味同一件事算兩次"""
        self._add_remember(qq, tag.get("remember"), str(event.get_group_id() or ""))

    def remember_from(self, qq: str, text: str, gid: str = "") -> bool:
        """沒有 event 的地方（mind 發完複查撤回了一條帶「记住」行的、心跳說話拿掉了那行）替她記一條记住，跟聊天裡當場記同一套（每天上限、source=live）"""
        return self._add_remember(str(qq), clean_remember(text), str(gid or ""))

    def _add_remember(self, qq: str, text: str | None, gid: str) -> bool:
        if not text or not qq or not self._remember_on():
            return False
        cap = int(self.s.get("remember_daily", REMEMBER_DAILY) or 0)
        if cap and self.heart.live_today(qq) >= cap:
            logger.info(f"[lively_bond] 今天当场记 {qq} 已经 {cap} 条了，这条不记：{text}")
            return False
        mid = self.heart.add_moment(qq, time.time(), text, kind="日常", weight=0.4, impacts={},
                                    scope=f"group:{gid}" if gid else "dm", private=not gid, source="live")
        if mid:
            logger.info(f"[lively_bond] 当场记住 {qq}：{text}")
        return bool(mid)

    def _decide_sorry(self, event: AstrMessageEvent, qq: str, tag: dict):
        """這一輪告訴過她 TA 像在道歉：寫了 |原谅 就照誠懇道歉的規則降一級，沒寫就記成沒原諒（晚上回味照她當時定的）"""
        seen = event.get_extra("lively_bond_sorry")
        if not seen:
            return
        now = time.time()
        if tag.get("forgive"):
            before, after = self.heart.forgive(qq, now, seen["text"])
            note = f"原谅了：{ATTITUDES[before]}→{ATTITUDES[after]}｜{seen['text']}"
            self.heart.add_moment(qq, now, f"TA 道歉，{self.s.me()}原谅了：{seen['text']}"[:40], kind="道歉", weight=0.6,
                                  impacts={"芥蒂": -1}, scope=f"group:{event.get_group_id()}" if event.get_group_id() else "dm",
                                  private=not event.get_group_id(), source="live")
        else:
            note = f"没原谅：{ATTITUDES[seen['att']]}｜{seen['text']}"
        self.store.log(qq, "sorry", 0, 0, note[:200], self._source(event), now)
        self.store.db.commit()
        logger.info(f"[lively_bond] {qq} 道歉，她{note}")

    # 存進聊天記錄前，把助手消息裡的標記也刪掉
    @filter.on_agent_done(priority=100001)
    async def clean_history(self, event: AstrMessageEvent, run_context, resp):
        said = {_flat(event.get_extra("lively_bond_remember") or ""), *(event.get_extra("lively_bond_narrated") or [])} - {""}

        def clean(t: str) -> str:
            t = strip_tags(t)
            if said:
                t = "\n".join(l for l in t.split("\n") if _flat(l) not in said)
            return t

        try:
            # 這一輪每一步都清（調工具前那步帶的標記也會存進去）
            for msg in reversed(run_context.messages):
                if msg.role == "user":
                    break
                if msg.role != "assistant":
                    continue
                if isinstance(msg.content, str):
                    msg.content = clean(msg.content)
                elif isinstance(msg.content, list):
                    for part in msg.content:
                        if isinstance(part, TextPart) and part.text:
                            part.text = clean(part.text)
        except Exception as e:
            logger.error(f"[lively_bond] 清理历史标记失败: {e}")

    # 兜底：發出去之前再掃一遍
    @filter.on_decorating_result(priority=100001)
    async def last_check(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not isinstance(result.chain, list):
            return
        chain, changed = [], False
        for comp in result.chain:
            if isinstance(comp, Plain) and (has_tag(comp.text) or "bond_note" in comp.text):
                lines = remember_lines_of(comp.text)
                text, tag = parse_tag(NOTE_RE.sub("", comp.text))
                self._note_remember_lines(event, lines, tag)
                text = self._take_narrated(event, text, tag)
                self._apply_tag(event, tag)
                changed = True
                if not text.strip():
                    continue
                comp = Plain(text)
            elif (isinstance(comp, Plain) and "\n" in comp.text.strip()
                  and _flat(comp.text) != event.get_extra("lively_bond_narr_seen")):
                # 調工具前那步帶的字不經過 on_llm_response：最後一行像记住的在這裡拿掉
                text = self._take_narrated(event, comp.text, None)
                if text != comp.text:
                    changed = True
                    if not text.strip():
                        continue
                    comp = Plain(text)
            chain.append(comp)
        # 這一輪標記裡记住過的事，後面那步又寫成了正文的一行（「手绘一张一亿 token 的鲸元券塞给本鱼」）：拿掉
        said = {_flat(event.get_extra("lively_bond_remember") or ""), *(event.get_extra("lively_bond_narrated") or [])} - {""}
        if said:
            for i, comp in enumerate(chain):
                if isinstance(comp, Plain):
                    lines = [l for l in comp.text.split("\n") if _flat(l) not in said]
                    if len(lines) != len(comp.text.split("\n")):
                        chain[i], changed = Plain("\n".join(lines).strip()), True
            chain = [c for c in chain if not isinstance(c, Plain) or c.text.strip()]
        if changed:
            logger.warning(f"[lively_bond] 发送前拦掉了残留标记或写给自己的记住: {event.unified_msg_origin}")
            if not any(not isinstance(c, Plain) or c.text.strip() for c in chain):
                event.stop_event()
                return
            result.chain = chain

    # ---------- 情感：回味記憶 ----------
    def _seed_hearts(self):
        """第一次用：把舊的人物卡和印象搬成「心裡的 TA」，以後每晚按新片段更新"""
        if self.store.get_meta("heart_seeded"):
            return
        n = 0
        for p in self.store.db.execute("SELECT qq, impression FROM people").fetchall():
            card = self.store.get_card(p["qq"])
            hist = [(e["ts"], e["note"]) for e in self.store.events(p["qq"], 60) if e["kind"] == "impression"]
            if not card and not p["impression"]:
                continue
            self.heart.set_heart(p["qq"], heart_from_card(card, p["impression"], hist))
            n += 1
        self.store.set_meta("heart_seeded", str(time.time()))
        logger.info(f"[lively_bond] 把 {n} 个人的人物卡和印象搬进了心里的 TA")

    def _retire_cards(self):
        """10-24 停掉每週人物卡：只有卡、心裡還沒有 TA 的人先搬進去（只做一次）"""
        if self.store.get_meta("cards_retired"):
            return
        n = 0
        for (qq,) in self.store.db.execute(
                "SELECT c.qq FROM cards c LEFT JOIN hearts h ON h.qq = c.qq WHERE h.qq IS NULL").fetchall():
            p = self.store.get(qq) or {"impression": ""}
            hist = [(e["ts"], e["note"]) for e in self.store.events(qq, 60) if e["kind"] == "impression"]
            self.heart.set_heart(qq, heart_from_card(self.store.get_card(qq), p["impression"], hist))
            # 當成還沒整理過：以後有片段就整理成完整的，以前的片段也算進去
            self.store.db.execute("UPDATE hearts SET updated = 0 WHERE qq = ?", (qq,))
            n += 1
        self.store.set_meta("cards_retired", str(time.time()))
        logger.info(f"[lively_bond] 每周人物卡停了：{n} 个只有人物卡的人搬进了心里的 TA")

    async def reflect(self) -> str:
        if not self.s.get("reflect", True):
            return "回味已关闭"
        async with self._card_lock:
            docs, moms, fail = await self.digest.digest()
            ok, hfail = await self.digest.update_hearts()
            eased = self.ease_all()
        await self._tell_closed()
        msg = (f"回味：读了 {docs} 条记忆，记下 {moms} 个片段，更新心里的 TA {ok} 个"
               + (f"，态度缓和 {eased} 个" if eased else "")
               + (f"，失败 {fail + hfail} 次" if fail or hfail else ""))
        logger.info(f"[lively_bond] {msg}")
        return msg

    async def _tell_closed(self):
        """回味時有人到了封閉（4 級）：事實交給〈我〉寫一句告訴〈主人〉（10-28 她自己選的「要，〈我〉自己写一句」）。
        〈主人〉诅咒的人到了冷處理（3 級）也說一聲：照诅咒〈我〉照樣會去對線，〈主人〉沒叫停就照舊（10-30 用戶定；11-03 起仇人搬進诅咒）"""
        closed, self.digest.closed = self.digest.closed, []
        for c in closed:
            when = gday(c["ts"])
            if c.get("enemy"):
                level = ATTITUDES[min(int(c.get("level") or 3), len(ATTITUDES) - 1)]
                facts = (f"〈我〉回想聊天记录时，对「{c['name']}」的态度到了{level}：{when} 在{c['where']}，{c['text']}。"
                         "TA 在〈主人〉的诅咒名单上，〈我〉遇上了照样会去对线，不会因为冷处理就躲开；"
                         "〈主人〉要〈我〉停，〈我〉用 lively_curse 解咒就停")
                card = (f"她对「{c['name']}」（QQ {c['qq']}）的态度到了{level}，这人在你的诅咒名单上\n"
                        f"为的是 {when} {c['where']}：{c['text']}\n"
                        "默认照旧对线；要停就跟她说解咒")
                try:
                    await tell_owner(self.s, facts, "诅咒的人到了" + level, must=True, card=card, kind="仇人")
                except Exception as e:
                    logger.error(f"[lively_bond] 仇人的事没告诉成主人: {e}")
                continue
            facts = (f"〈我〉回想聊天记录时，对「{c['name']}」的态度到了封闭（完全不想理 TA）：{when} 在{c['where']}，{c['text']}。"
                     "之后 TA 说话〈我〉照样看得到，回不回〈我〉自己定")
            card = (f"她对「{c['name']}」（QQ {c['qq']}）的态度到了封闭\n为的是 {when} {c['where']}：{c['text']}\n"
                    f"想改回来：/好感 态度 {c['qq']} 0")
            try:
                await tell_owner(self.s, facts, "态度到了封闭", must=True, card=card, kind="封闭")
            except Exception as e:
                logger.error(f"[lively_bond] 封闭的事没告诉成主人: {e}")

    def ease_all(self) -> int:
        """冷淡、戒備沒再犯的慢慢緩和。返回緩和了幾個"""
        self._eased_at = time.time()
        eased = 0
        for st in self.heart.stanced(1):
            after = self.heart.ease(st["qq"])["attitude"]
            if after < st["attitude"]:
                eased += 1
                logger.info(f"[lively_bond] 态度缓和 {st['qq']}：{ATTITUDES[st['attitude']]} → {ATTITUDES[after]}")
        return eased

    async def _card_loop(self):
        await asyncio.sleep(300)
        while True:
            try:
                self._sync_owners()
                if time.time() - self._eased_at >= EASE_EVERY:
                    self.ease_all()
                hm = datetime.now(TZ).strftime("%H:%M")
                # 回味：凌晨到早上做；第一次會把以前的記憶補一遍，也不用等到凌晨
                if "04:00" <= hm < "09:00" or not self.store.get_meta("reflect_init"):
                    await self.reflect()
                    if self.digest.last_docs < DOCS_PER_RUN:  # 以前的記憶補完了
                        self.store.set_meta("reflect_init", "1")
                elif self.s.get("reflect", True) and self.digest.points_source() is not None:
                    # 片段從記憶點轉（第 2 步）不叫模型：白天也轉，感情當天就跟著變；心裡的 TA 照舊凌晨整理
                    async with self._card_lock:
                        docs, moms, _ = await self.digest.digest()
                    if moms:
                        logger.info(f"[lively_bond] 从记忆插件转了 {docs} 批、{moms} 个片段")
                    await self._tell_closed()
                # 以前的人還沒有專屬回憶：照以前的片段讓她挑一次，每半小時幾個人（之後每晚整理心裡的 TA 時一起挑）
                if self.s.get("heart_memories", True) and self.digest.memories_due():
                    async with self._card_lock:
                        ok, mfail = await self.digest.pick_memories()
                    logger.info(f"[lively_bond] 挑专属回忆：{ok} 个人" + (f"，失败 {mfail} 个" if mfail else ""))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[lively_bond] 背景任务出错: {e}", exc_info=True)
            await asyncio.sleep(1800)

    # ---------- 群裡要印象卡：只發印象卡，不發檔案 ----------
    async def _qq_avatar(self, qq: str) -> bytes | None:
        for size in (640, 100):
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                    async with s.get(f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s={size}") as r:
                        body = await r.read()
                if r.status == 200 and body[:3] in (b"\xff\xd8\xff", b"\x89PN", b"GIF"):
                    return body
            except Exception:
                continue
        return None

    @filter.after_message_sent()
    async def card_promised(self, event: AstrMessageEvent):
        """提醒过要卡、她话里说了在给却没调工具：话发完后补发这张卡（只补她自己说了要给的）"""
        if not event.get_extra("lively_card_hinted") or event.get_extra("lively_card_sent") or event.get_extra("lively_card_done"):
            return
        result = event.get_result()
        text = "".join(c.text for c in (result.chain if result and isinstance(result.chain, list) else []) if isinstance(c, Plain))
        if not CARD_GIVE_RE.search(text) or CARD_REFUSE_RE.search(text):
            return
        ok, msg = await self._send_card(event)
        logger.warning(f"[lively_bond] 她说了给看却没调工具，补发印象卡：{'发了' if ok else msg}「{text[:30]}」")

    @filter.llm_tool("lively_impression_card")
    async def share_card(self, event: AstrMessageEvent, who: str = ""):
        """把印象卡发到当前聊天：一张图片，画着你对 TA 的印象、好感度、熟悉度、聊过几次，投喂过的带档位。默认发正在说话的这个人自己的那张；只发图，不带文字。有人说「印象卡」「人物卡」「你对我什么印象」「我在你心里是什么样的」「我好感度多少」之类，多半就是想看这张。别人想看、你也愿意给的时候可以用。
        who：〈主人〉在这条消息里请你发另一个人的卡时才写，写那个人的 QQ 号或小本本上的名字；发不发、发谁的由你看情况定。别人请你发第三个人的卡发不了。
        Args:
            who(string): 要发谁的卡（QQ 号或名字），不写就是正在说话的这个人自己的
        """
        done = event.get_extra("lively_card_done")
        if done:
            return f"这条消息已经处理过印象卡了（{done}），这次没再发"
        target = ""
        who = str(who or "").strip()
        if who and who != str(event.get_sender_id() or ""):
            if not self.s.is_owner(str(event.get_sender_id() or "")):
                return "没发：只有〈主人〉请你的时候才能发别人的卡，别人只能看自己的"
            if who.isdigit() and 5 <= len(who) <= 12:
                target = who
            else:
                rows = self.store.db.execute("SELECT qq FROM people WHERE name=?", (who,)).fetchall() or \
                    self.store.db.execute("SELECT qq FROM people WHERE name LIKE ?", (f"%{who}%",)).fetchall()
                if len(rows) != 1:
                    return f"没发：认不出「{who}」是谁（" + ("小本本上没有叫这个名字的人" if not rows else f"对得上 {len(rows)} 个人") + "），who 写 QQ 号"
                target = str(rows[0][0])
        # 這個聊天剛發過卡：多半是跟著要的（「我呢」「我也要」），靠上下文才是要卡，不學
        now = time.time()
        follow = any(now - t < FOLLOW_GAP for t in self._shared_umo.get(event.unified_msg_origin, []))
        ok, msg = await self._send_card(event, target)
        if target:
            return self.s.fill(msg)
        hinted = bool(event.get_extra("lively_card_hinted"))
        if hinted:
            logger.info(f"[lively_bond] 提醒过的印象卡，她决定发：{'发了' if ok else msg}")
        # 語義沒認出來、模型自己判斷出是在要卡：記成新例句，下次直接認（提醒過的本來就認出來了）
        if ok and not follow and not hinted and self.s.get("card_learn", True):
            learned = self.intent.add(event.message_str or "", yes=True, auto=True)
            if learned:
                logger.info(f"[lively_bond] 学到一句要印象卡的说法：「{learned}」")
        return self.s.fill(msg)

    @filter.llm_tool("lively_heart_edit")
    async def heart_edit(self, event: AstrMessageEvent, field: str = "", action: str = "", value: str = "", qq: str = "", why: str = ""):
        """记下或改掉你自己心里对一个人的记法（心里的 TA：称呼、印象、喜好、雷区、梗、最近、语气、牵挂，私下的牵挂和心事，还有你们俩的专属回忆）。
        新知道了关于某个人的事（自己看出来的，或者别人告诉你的）想记在 TA 身上，发现记错了、记混了人、TA 后来变了，
        或者别人请你记、请你改、你也认同的时候，都可以用。记的是那个人（qq），不一定是正在说话的这位。记下马上生效，之后说话就照新的；
        每晚回味时还会照着新聊到的事接着整理。谁都能记、能改（qq 写 TA 的号；不知道号可以先用 lively_memory_look 之类的找）；
        还没跟你聊过的号也能记：会先向 QQ 查这个号，查得到就在小本本上给 TA 建一页。
        私下牵挂、私下心事只有在和 TA 本人的私聊里（或〈主人〉的私聊里）才改得了。
        回忆是你和 TA 之间的专属回忆时刻（最多 6 个），哪些算由你定；TA 跟你说话时会带上，私聊里的在群里也带、标着「私聊里的」。
        在和 TA 本人的私聊里加的算私聊里的事，在别处加的不算，开头写「私聊」就算。

        Args:
            field(string): 改哪一栏：称呼、印象、喜好、雷区、梗、最近、语气、牵挂、私下牵挂、私下心事、回忆
            action(string): 「改成」整栏换掉（列表栏用、分开写几条，写空就是清空）；「加」往列表里加一条；「删」删掉含 value 的那条
            value(string): 内容；梗可以写成「梗（来历）」；回忆可以在开头写哪天（「10-26 …」），不写就是今天
            qq(string): 改谁，QQ 号；不写就是正在说话的这个人
            why(string): 为什么改，一句话
        """
        self._sync_owners()
        speaker, _ = self._who(event)
        target = str(qq or "").strip() or speaker or ""
        if not target.isdigit():
            return "没改：不知道要改谁（qq 写 QQ 号）"
        dm = not event.get_group_id()
        owner_dm = dm and self.s.is_owner(speaker)
        # 以前只能改正在說話的人和這句提到的人（主人私聊除外）；10-28 用戶定「放开」，誰都能改，私下那兩欄照舊
        if str(field or "").strip().startswith("私下") and not (owner_dm or (dm and target == speaker)):
            return "没改：私下的那两栏只有在和 TA 本人的私聊里（或〈主人〉的私聊里）才改得了"
        made = ""
        if self.store.get(target) is None and target not in core.OWNERS:
            # 10-29：以前沒聊過的人記不了（她 08:15 想給主人說的 bot 號記一筆，回「小本本上还没有」，她卻說成記下了）。
            # 現在先向 QQ 查這個號，查得到就先建一頁（熟悉度、聊過的次數都是 0），查不到不建
            ok, msg = edit_heart({}, str(field or ""), str(action or ""), str(value or "").strip()[:120])
            if ok is None:  # 欄位、寫法不對就別先建一頁
                return self.s.fill("没改：" + msg)
            nick = await self._stranger_nick(target)
            if nick is None:
                return self.s.fill("没改：小本本上还没有这个号，向 QQ 也没查到这个号是谁（号写错了，或者 QQ 这会儿连不上）")
            self.store.save(self.store.load(target, nick))
            made = f"（小本本上原来没有 TA，这次先建了一页：QQ 昵称「{nick or '查不到'}」，还没聊过）"
        value = str(value or "").strip()[:120]
        private = dm and target == speaker
        if str(field or "").strip() == "回忆" and value.startswith("私聊"):
            private, value = True, re.sub(r"^(?:里的?)?[：:，,\s]*", "", value[2:])
        ok, msg = self.heart.edit(target, str(field or ""), str(action or ""), value, private=private)
        if not ok:
            return self.s.fill("没改：" + msg)
        note = f"{msg}" + (f"｜{str(why).strip()[:40]}" if str(why or "").strip() else "")
        self.store.log(target, "heart_edit", 0, 0, note[:200], self._source(event))
        self.store.db.commit()
        logger.info(f"[lively_bond] 她自己改了心里的 TA {target}：{note}{made}")
        return self.s.fill(f"改好了：{msg}{made}")

    async def _stranger_nick(self, qq: str) -> str | None:
        """向 NapCat 查一個號的 QQ 暱稱；查不到（號不存在、連不上）回 None，暱稱是空的回空字串"""
        if not re.fullmatch(r"\d{5,11}", qq):
            return None
        client = self.s.client()
        if client is None:
            return None
        try:
            r = await asyncio.wait_for(client.api.call_action("get_stranger_info", user_id=int(qq)), 8)
        except Exception as e:
            logger.debug(f"[lively_bond] 查 {qq} 是谁失败：{e}")
            return None
        if not isinstance(r, dict) or not (r.get("user_id") or r.get("uin") or r.get("nickname")):
            return None
        return str(r.get("nickname") or r.get("nick") or "").strip()[:30]

    async def _send_card(self, event: AstrMessageEvent, target: str = "") -> tuple[bool, str]:
        """把要卡的人自己的印象卡發到當前聊天（target 是〈主人〉請她發的別人的號）。返回 (發了沒有, 給模型看的說明)"""
        qq, name = (target, "") if target else self._who(event)
        if not qq:
            return False, "没发：不知道是谁在要。" + CARD_NOT_SENT
        now = time.time()
        umo = event.unified_msg_origin
        key = (qq, umo)
        if key in self._card_busy:
            return False, "TA 的印象卡这会儿正在发（画卡、发送要几秒），这次没再发一张"
        # 一個聊天一小時 8 張那道 10-28 拿掉了（用戶定「放开」）：發幾張她定；這裡只記最近一小時發過的，判斷「跟著要的」用
        recent = [t for t in self._shared_umo.get(umo, []) if now - t < 3600]
        if not self.store.get(qq):
            return False, "没发：小本本上还没有 TA（还没跟〈我〉聊过），还没有 TA 的卡。" + CARD_NOT_SENT
        p = self.store.load(qq, name, now)
        r = self.rel.of(qq, now)
        mins = int((now - (p["last_talk"] or now)) // 60)
        last = ("刚刚聊过" if mins < 1 else f"最近聊天 {mins} 分钟前" if mins < 60
                else f"最近聊天 {mins // 60} 小时前" if mins < 1440 else f"最近聊天 {mins // 1440} 天前")
        out = Path(StarTools.get_data_dir("astrbot_plugin_lively_bond")) / "card_img"
        out.mkdir(exist_ok=True)
        fed = r.get("fed") or {}
        feed = {"tier": fed["tier"], "name": fed["tier_name"]} if fed.get("tier") else None
        own = self.card_bgs().get(qq)
        # 先佔上：畫卡、發送這幾秒裡同一個人又要一次，不再畫第二張
        self._card_busy.add(key)
        self._shared_umo[umo] = recent + [now]
        try:
            made = self.made_avatars().get(qq)
            avatar, face = await asyncio.to_thread(card_img.made_faces, made) if made else (None, None)
            if avatar is None:
                made = None
                avatar = await self._qq_avatar(qq)
            path = await asyncio.to_thread(  # 投喂第四檔起是動圖（.gif）
                card_img.render, out / f"card_{qq}", qq=qq, name=p["name"] or name or qq, favor=r["score"],
                favor_desc=r["desc"], stage=self.s.fill(stage(p)), mood=mood_shown(p), mood_kind=p["mood"],
                known_days=r.get("known_days") or 0, talks=p["talks"], moments=self.heart.moment_count(qq),
                words=(r.get("feel") or {}).get("public") or [], impression=r["impression"],
                met=datetime.fromtimestamp(p["first_met"] or now, TZ).strftime("%m-%d %H:%M"), last=last,
                avatar=avatar, face=face, owner=self.s.is_owner(qq), me=self.s.me(), feed=feed, bg=own and own["path"],
                alias=(self.heart.get_heart(qq) or {}).get("称呼") or "", bot=((r.get("bot_info") or {}).get("vendor") or True) if r.get("bot") else False,
                card=self.store.card_in(qq, str(event.get_group_id())) if event.get_group_id()
                else next(iter(self.store.cards_of(qq).values()), ""))
            t0 = time.time()
            try:
                await self.context.send_message(umo, MessageChain().file_image(str(path)))
            except Exception as e:
                # QQ 那邊偶爾報發送超時（napcat retcode 1200），卡片多半其實發到了：先翻那邊的消息，沒看到才再發一次
                # （10-28 19:30 白岩要卡，報超時後 3 秒直接重發，群裡收到兩張）
                if "Timeout" not in str(e) and "1200" not in str(e):
                    raise
                logger.warning(f"[lively_bond] 发印象卡报超时，翻一下那边看到了没有: {umo}")
                if await self._card_landed(umo, t0):
                    logger.info(f"[lively_bond] 那张印象卡其实发到了（QQ 报的超时）: {umo}")
                else:
                    logger.warning(f"[lively_bond] 没看到那张印象卡，再发一次: {umo}")
                    await self.context.send_message(umo, MessageChain().file_image(str(path)))
        except Exception as e:
            logger.error(f"[lively_bond] 发印象卡失败: {e}", exc_info=True)
            self._shared_umo[umo] = [t for t in self._shared_umo.get(umo, []) if t != now]
            return False, "没发：发送时 QQ 那边出错了。" + CARD_NOT_SENT
        finally:
            self._card_busy.discard(key)
        event.set_extra("lively_card_sent", True)
        self.store.log(qq, "card_shared", 0, 0, "群里" if event.get_group_id() else "私聊", self._source(event), now)
        self.store.db.commit()
        logger.info(f"[lively_bond] 给 {p['name'] or qq}({qq}) 发了印象卡：{umo}")
        look = f"这张带着 TA 的投喂档位「{feed['name']}」：{card_img.fx.LOOK[feed['tier']]}。" if feed else ""
        if own and path.suffix != ".png":  # 用了專屬背景（讀不出來會退回原來的樣子）
            look = ("这张用的是专门给 TA 画的背景" + (f"：{self.s.fill(own['look'])}" if own["look"] else "")
                    + "，香槟金的头像圈和细框、金字名字"
                    + (f"；右上角标着 TA 的投喂档位「{feed['name']}」" if feed else "")
                    + ("，卡上会慢慢扫过一道光（动图）" if path.suffix == ".gif" else "") + "。")
        if made:
            look += "卡上的图是 TA 定做的虚拟形象，不是 QQ 头像。"
        return True, f"印象卡已经发出去了（只发了卡片，没带别的话）。{look}"

    async def _card_landed(self, umo: str, t0: float) -> bool:
        """發卡報超時以後翻那邊最近的消息：t0 之後有〈我〉發的帶圖的那條就算發到了（和 comic 的 _landed 一樣）"""
        client, bot = self.s.client(), str(self.s.bot_qq() or "")
        if client is None or not bot:
            return False
        tail = umo.rsplit(":", 1)[-1]
        action, kw = (("get_friend_msg_history", {"user_id": int(tail)}) if ":FriendMessage:" in umo
                      else ("get_group_msg_history", {"group_id": int(tail)}))
        waited = 0
        for at in CARD_CHECK:
            await asyncio.sleep(max(0, at - waited))
            waited = at
            try:
                res = await client.api.call_action(action, count=20, **kw)
            except Exception as e:
                logger.debug(f"[lively_bond] 翻 {umo} 的消息失败：{e}")
                continue
            for m in (res or {}).get("messages") or []:
                if (isinstance(m, dict) and str((m.get("sender") or {}).get("user_id", "")) == bot
                        and float(m.get("time") or 0) >= t0 - 5
                        and any(isinstance(x, dict) and x.get("type") == "image" for x in m.get("message") or [])):
                    return True
        return False

    # ---------- 主人指令 ----------
    @filter.command("好感")
    async def admin(self, event: AstrMessageEvent, args: GreedyStr):
        if not self.s.is_owner(event.get_sender_id()):
            return  # 別人發的當普通聊天處理
        if event.get_group_id():
            await event.send(MessageChain().message(self.s.admin_text("好感指令只在私聊里用")))
            event.stop_event()
            return
        a = (args or "").split()
        if a[:2] == ["人物卡", "更新"]:
            await event.send(MessageChain().message(self.s.admin_text(
                "人物卡停了，心里的 TA 每晚回味时更新；要马上整理就发 /好感 整理")))
            event.stop_event()
            return
        if a[:1] == ["整理"]:
            await event.send(MessageChain().message(self.s.admin_text("开始回味最近的新记忆，做完再回你")))
            event.stop_event()
            await event.send(MessageChain().message(self.s.admin_text(await self.reflect())))
            return
        reply = self._handle_admin(a)
        await event.send(MessageChain().message(self.s.admin_text(reply)))
        event.stop_event()

    def _examples_admin(self, a: list[str]) -> str:
        it = self.intent
        if a and a[0] in ("加", "算") and len(a) >= 2:
            t = it.add(" ".join(a[1:]), yes=True)
            return f"记住了，「{t}」算在要印象卡" if t else "这句太长或是空的"
        if a and a[0] == "不算" and len(a) >= 2:
            t = it.add(" ".join(a[1:]), yes=False)
            return f"记住了，「{t}」不算在要印象卡" if t else "这句太长或是空的"
        if a and a[0] == "删" and len(a) >= 2 and a[1].isdigit():
            t = it.remove(int(a[1]))
            return f"删掉了「{t}」" if t else "没有这个编号"
        d = it.learned()
        yes, no = it.examples()
        lines = [f"起步例句：要卡 {len(yes) - len(d['yes'])} 句、不算 {len(no) - len(d['no'])} 句（在插件配置里改）"]
        both = [("要卡", x) for x in d["yes"]] + [("不算", x) for x in d["no"]]
        lines += [f"{i}. [{k}] {x}" for i, (k, x) in enumerate(both, 1)] or ["还没学到新说法"]
        lines.append("/好感 卡片例句 加 句子 / 不算 句子 / 删 编号")
        return "\n".join(lines)

    def _handle_admin(self, a: list[str]) -> str:
        s = self.store
        if not a:
            return HELP
        cmd = a[0]
        if cmd == "列表":
            rows = [s.settle(p, time.time()) for p in s.recent(15)]
            if not rows:
                return "还没有记录"
            since = self.rel.last_digest()
            out = []
            for p in rows:
                r = self.rel.of(p["qq"], since=since)
                out.append(f"{p['name'] or '?'}({p['qq']}) {r['tier']} 好感度{r['score']:g} 熟悉{p['familiarity']:.0f} "
                           + (f"态度:{r['attitude_name']} " if r["attitude"] else "")
                           + f"心情:{mood_shown(p)} 上次:{_fmt_time(p['last_seen'])}")
            return "\n".join(out)
        if cmd == "状态":
            st = s.self_state()
            return f"〈我〉现在：{st}"
        if cmd == "卡片例句":
            return self._examples_admin(a[1:])
        if cmd == "群梗":
            gs = self.group.gags(a[1] if len(a) > 1 and a[1].isdigit() else "")
            if not gs:
                return "还没记下群梗（每晚回味群聊记忆时记，只在后台）"
            return "群梗（只记在后台，还没带给她）：\n" + "\n".join(
                f"{g['gid']} 「{g['gag']}」" + (f"（{g['origin']}）" if g["origin"] else "")
                + (f" 常玩：{'、'.join(g['who'])}" if g["who"] else "") + f" ×{g['n']} 最近 {gday(g['last'])}" for g in gs)
        if len(a) < 2 or not a[1].isdigit():
            return HELP
        qq = a[1]
        if cmd == "回忆":
            moms = self.heart.moments(qq, 12)
            if not moms:
                return "和 TA 还没有记下的片段"
            return f"和 {qq} 的片段（共 {self.heart.moment_count(qq)} 条，新的在前）：\n" + "\n".join(
                f"{_fmt_time(m['ts'])} {'私聊' if m['private'] else '群里'} [{m['kind']}/{m['feeling']}] {m['text']}"
                + (f" {feelings_text(m['impacts'])}" if m["impacts"] else "") for m in moms)
        if cmd in ("心里", "人物卡"):  # 人物卡停了，看的就是心裡的 TA
            h = self.heart.get_heart(qq)
            if not h:
                return "心里还没有 TA 的样子（有了片段以后每晚会整理）"
            hist = "；".join(f"{x['印象']}（到{x['到']}）" for x in h.get("印象史") or [])
            return (f"{qq} 在〈我〉心里（{_fmt_time(h.get('_updated') or 0)} 更新）：\n{heart_text(h)}"
                    + (f"\n以前的印象：{hist}" if hist else ""))
        if cmd == "关系":
            ps = self.group.pairs_of(qq)
            if not ps:
                return "还没记下 TA 和别的群友之间的事（每晚回味群聊记忆时记，只在后台）"
            def nm(q):
                return (s.get(q) or {}).get("name") or q
            return f"{nm(qq)}（{qq}）和别的群友（只记在后台）：\n" + "\n".join(
                f"{nm(p['b'] if p['a'] == qq else p['a'])}（{p['b'] if p['a'] == qq else p['a']}）：{p['how']}"
                f"（群 {p['gid']}，{gday(p['ts'])}，记过 {p['n']} 次）" for p in ps)
        if cmd == "感情":
            st = self.heart.get_stance(qq)
            f = self.heart.feelings(qq)
            lines = [f"{qq} 的感情：{feelings_text(f) or '还没有'}（片段 {self.heart.moment_count(qq)} 条）",
                     f"态度：{ATTITUDES[st['attitude']]}" + (f"（{_fmt_time(st['changed'])} {st['reason']}）" if st["reason"] else ""),
                     f"犯过 {st['offenses']} 次，诚恳道歉 {st['apologies']} 次"]
            return "\n".join(lines)
        if cmd == "态度" and len(a) >= 3:
            if not a[2].isdigit() or not 0 <= int(a[2]) < len(ATTITUDES):
                return "态度写 0～4：" + "、".join(f"{i} {x}" for i, x in enumerate(ATTITUDES))
            st = self.heart.admin_attitude(qq, int(a[2]))
            return f"对 {qq} 的态度改成：{ATTITUDES[st['attitude']]}（说话时会带上这个态度，不拦〈我〉说话）"
        if cmd == "查":
            p = s.get(qq)
            if not p:
                return "没有这个人的记录"
            p = s.settle(p, time.time())
            r = self.rel.of(qq)
            lines = [
                f"{p['name'] or '?'}({qq})：{stage(p)}，{r['desc']}（{r['tier']}）",
                f"好感度 {r['score']:g}（回忆 {r['base']:g}，回味后聊天 {r['live']:+g}" + (f"，手调 {r['adjust']:+g}" if r["adjust"] else "")
                + f"）；旧好感 {p['favor']:.1f}（只留着看）",
                f"熟悉度 {p['familiarity']:.0f}，聊过 {p['talks']} 次",
                f"心情 {mood_shown(p)}（{p['mood']}，强度 {p['mood_level']}）",
                f"印象 {p['impression'] or '-'}（聊天时记的）",
                f"心里的 TA {heart_text(self.heart.get_heart(qq)) or '-'}",
                f"感情 {feelings_text(self.heart.feelings(qq)) or '-'}，态度 {ATTITUDES[self.heart.get_stance(qq)['attitude']]}",
                f"初见 {_fmt_time(p['first_met'])}，上次见 {_fmt_time(p['last_seen'])}，上次聊 {_fmt_time(p['last_talk'])}",
            ]
            ev = s.events(qq)
            if ev:
                lines.append("最近变化：")
                lines += [f"  {_fmt_time(e['ts'])} {e['kind']} {e['delta']:+.1f} {e['note']}" for e in ev]
            return "\n".join(lines)
        if cmd == "设" and len(a) >= 4:
            field = {"好感": "favor", "好感度": "favor", "熟悉": "familiarity", "熟悉度": "familiarity"}.get(a[2])
            if not field:
                return "只能设 好感 或 熟悉"
            if self.s.is_owner(qq):
                return "主人的好感和熟悉度固定是满的，改不了"
            try:
                value = float(a[3])
            except ValueError:
                return "数值不对"
            if field == "favor":  # 好感度：-80～80，記成偏移，之後照樣跟著回憶和聊天變
                r = self.rel.set_score(qq, value)
                return f"好了：{r['name'] or qq} 好感度现在 {r['score']:g}（{r['tier']}）"
            p = s.admin_set(qq, field, value)
            return f"好了：{p['name'] or qq} 现在是 {stage(p)}"
        if cmd == "印象" and len(a) >= 3:
            text = "" if a[2] == "清空" else " ".join(a[2:])
            s.admin_set(qq, "impression", text)
            if text:
                self.heart.note_impression(qq, text)
            return "印象已清空" if not text else f"印象改成：{text[:20]}"
        if cmd == "重置":
            s.reset(qq)
            self.heart.reset(qq)
            return f"{qq} 的记录已清空，下次见面重新认识"
        return HELP
