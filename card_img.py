"""把面板「印象卡冊」裡的那張印象卡畫成圖片，群友要印象卡時發進群裡。

照著面板的 .fcard 樣式畫（2 倍尺寸）：漸變卡框、QQ 頭像、心情、好感、名字、關係、好感條、熟悉/聊過/印象、機器人覺得、認識時間。
只畫卡片，不畫檔案（好感變化、記憶、說過的話、小本本）
投喂過的人換一種卡（card_fx.py）：整張小肥魚的背景畫＋霧面玻璃＋圓形頭像，按飯錢檔位換背景和材質；第四、五檔是動圖（GIF）。卡上不寫金額
"""
import re
import unicodedata
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import card_emoji as emo
from . import card_fx as fx

HERE = Path(__file__).parent / "card_assets"


def _first(*paths: str) -> Path:
    for p in paths:
        if Path(p).exists():
            return Path(p)
    return Path(paths[0])


# 手寫體放在 card_assets/rt.woff2（倉庫裡不帶，見 README）；沒有就整張卡用黑體
HAND_FONT = HERE / "rt.woff2"
# 中文黑體：常見系統路徑挨個找（Debian/Ubuntu 的 fonts-noto-cjk、其他發行版、macOS、Windows）
BODY_FONT = _first(
    str(HERE / "body.ttc"), str(HERE / "body.ttf"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)
BOLD_FONT = _first(
    str(HERE / "bold.ttc"), str(HERE / "bold.ttf"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    str(BODY_FONT),
)
emo.use_text_font(BOLD_FONT)  # 黑體畫得出來的符號（♡ ☆）照舊當字寫，畫不出來的才去表情字體找
FISH = HERE / "fish.webp"

S = 2  # 面板裡卡片約 380px 寬，按 2 倍畫
W = 380 * S
TIERS = {  # 和麵板 .fcard.t-* 一樣
    "t-owner": ("#c9921f", "#f7e2a4", "#8a5c00"), "t-love": ("#2f5fd0", "#a9d8f5", "#1c2b66"),
    "t-close": ("#5fa8e6", "#d6eefc", "#2346a8"), "t-mid": ("#9aa6c2", "#dfe7f5", "#5a6a96"),
    "t-hate": ("#d8506f", "#f8c9d6", "#9c2846"),
}
MOOD_DOT = {"开心": "#eaa92a", "得意": "#e0a630", "感动": "#2b9a6e", "害羞": "#ec7fa3", "吃醋": "#8fbf3f",
            "委屈": "#6f86d6", "小生气": "#e9724f", "担心": "#5b7bc4", "嫌弃": "#8a97b8", "平常": "#8f9cbd"}
INNER, INK, SOFT, FAINT, BLUE, RULE, QUOTE = "#fbfcff", "#1c2b66", "#5a6a96", "#8a97b8", "#2f5fd0", "#d3e3f4", "#eef5fc"


def tier_of(favor: float, owner: bool) -> str:
    if owner:
        return "t-owner"
    return "t-love" if favor >= 70 else "t-close" if favor >= 13 else "t-mid" if favor >= -13 else "t-hate"


def _font(path: Path, size: float):
    for p in (path, BODY_FONT):
        try:
            return ImageFont.truetype(str(p), int(size * S))
        except Exception:
            continue
    return ImageFont.load_default()


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a: str, b: str, k: float):
    return tuple(int(p + (q - p) * k) for p, q in zip(_rgb(a), _rgb(b)))


class Mixed:
    """軟糖體缺字（繁體、生僻字、符號）時逐字換成思源黑體；表情符號用彩色表情字體畫（見 card_emoji）"""

    def __init__(self, hand, fallback):
        self.hand, self.fb = hand, fallback
        self.size = getattr(hand, "size", 20)
        self._missing = bytes(hand.getmask("\U0010fffd"))
        self._cache: dict[str, bool] = {}

    def font_for(self, ch: str):
        if ch.isspace() or ch.isascii():
            return self.hand
        if ch not in self._cache:
            self._cache[ch] = bytes(self.hand.getmask(ch)) != self._missing
        return self.hand if self._cache[ch] else self.fb

    def width(self, d, unit: str) -> float:
        """一個字（clusters 切出來的）有多寬"""
        if emo.is_emoji(unit):
            return emo.advance(unit, self.size)
        return sum(d.textlength(c, font=self.font_for(c)) for c in unit)

    def length(self, d, text: str) -> float:
        return sum(self.width(d, u) for u in emo.clusters(text))

    def put(self, d, x: float, y: float, unit: str, fill) -> float:
        """在基線 y 上寫一個字，返回寫完的 x"""
        if emo.is_emoji(unit):
            img = getattr(d, "_image", None)
            if img is not None:
                emo.paste(img, x, y, unit, self.size)
            return x + emo.advance(unit, self.size)
        for c in unit:
            f = self.font_for(c)
            d.text((x, y), c, font=f, fill=fill, anchor="ls")
            x += d.textlength(c, font=f)
        return x

    def draw(self, d, xy, text: str, fill):
        x, y = xy
        for u in emo.clusters(text):
            x = self.put(d, x, y, u, fill)


def _diag(w: int, h: int, stops: list[tuple[float, str]]) -> Image.Image:
    """145° 斜向漸變（先畫小圖再放大，快）"""
    sw, sh = max(2, w // 8), max(2, h // 8)
    g = Image.new("RGB", (sw, sh))
    px = g.load()
    for y in range(sh):
        for x in range(sw):
            t = (x / sw * 0.57 + y / sh * 0.82) / 1.39
            for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
                if p0 <= t <= p1:
                    px[x, y] = _mix(c0, c1, (t - p0) / ((p1 - p0) or 1))
                    break
    return g.resize((w, h), Image.BICUBIC)


def _round_mask(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=r, fill=255)
    return m


CLOSERS = set("」』）)，。、！？!?,.：；…~～")


def _no_orphan(lines: list[str], fits) -> list[str]:
    """一行不以「」，。」這種收尾的標點開頭：前一行最後一個字跟著挪下來（挪了放得下才挪）"""
    for i in range(1, len(lines)):
        units = emo.clusters(lines[i - 1])
        if lines[i][:1] in CLOSERS and len(units) > 1 and units[-1] not in CLOSERS and fits(units[-1] + lines[i]):
            lines[i - 1], lines[i] = "".join(units[:-1]), units[-1] + lines[i]
    return lines


def _wrap(mixed: Mixed, d, text: str, width: float) -> list[str]:
    lines, cur = [], ""
    for ch in emo.clusters(text):
        if cur and mixed.length(d, cur + ch) > width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return _no_orphan(lines + [cur], lambda t: mixed.length(d, t) <= width)


def render(path: Path, *, qq: str, name: str, favor: float, favor_desc: str, stage: str, mood: str,
           known_days: float, talks: int, moments: int, impression: str, met: str, last: str,
           avatar: bytes | None, owner: bool = False, me: str = "我", mood_kind: str = "",
           feed: dict | None = None, bg: Path | None = None, face: bytes | None = None, alias: str = "",
           card: str = "", words: list | None = None, bot: bool | str = False) -> Path:
    """畫好存到 path，返回實際存的路徑（副檔名按這個換：沒投喂過 .png，投喂一～三檔 .jpg，第四檔起 .gif）。
    alias：〈我〉平時怎麼叫 TA（心裡的 TA 的「称呼」），卡上寫成「〈我〉叫你「…」」；空的、和名字一樣就不寫。
    card：TA 的群名片（發卡的那個群的，私聊時是最近記到的）；名字裡沒有字、稱呼裡也沒有字時拿它墊在名字旁邊（見 _names）。
    feed：{"tier": 1～5, "name": 檔名}，沒投喂過不傳。
    bg：TA 的專屬背景畫（見 card_fx.own_bgs）；有就用它畫成香檳金那種（.jpg，投喂四檔起照樣是動圖），讀不出來就當沒有。
    avatar 是普通卡上半那塊大圖；face 是投喂卡、專屬背景卡的圓頭像，不給就用 avatar（定做的形象兩張不一樣，見 made_faces）。
    known_days／talks／moments：認識幾天、直接聊過幾次、一起的回憶幾段（10-27 以前是熟悉度分數、聊過、印象改過幾次，群友看不懂）。
    words：〈我〉跟 TA 的感情短字（heart.feel 的 public：很亲、爱互损……），只放好的，疙瘩、反感不上卡
    bot：TA 是 bot（關係讀口的 bot，11-03），名字後面畫一個小機器人"""
    try:
        tier = max(0, min(fx.MAX_TIER, int((feed or {}).get("tier") or 0)))
    except (TypeError, ValueError):
        tier = 0
    name, alias = _names(name, alias, qq, card)
    mood = _printable(mood).strip() or mood_kind or "平常"
    impression = _printable(impression).strip()
    words = [w for w in (words or []) if w][:4]
    stats = ((f"{max(1, round(known_days or 0))}", "认识 · 天"), (str(talks), "聊过 · 次"), (str(moments), "回忆 · 段"))
    path = Path(path)
    art = None
    if bg:
        try:
            with Image.open(bg) as im:
                art = im.convert("RGB")
        except Exception:
            art = None
    if tier or art is not None:
        return _render_feed(path, tier, str((feed or {}).get("name") or "")[:6], qq=qq, name=name, favor=favor,
                            favor_desc=favor_desc, stage=stage, mood=mood, stats=stats, words=words,
                            impression=impression, met=met, last=last, avatar=face or avatar, me=me,
                            art=art, alias=alias, bot=bot)
    path = path.with_suffix(".png")
    t1, t2, t3 = TIERS[tier_of(favor, owner)]
    hand = lambda n: _font(HAND_FONT, n)
    body = lambda n: _font(BODY_FONT, n)
    bold = lambda n: _font(BOLD_FONT, n)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))

    pad = 9 * S
    bx = pad + 18 * S
    bw = W - 2 * bx
    qf = Mixed(hand(17), bold(16))
    lines = _clip_lines(_wrap(qf, probe, f"「{impression or '还没有印象～'}」", bw - 24 * S), 4,
                        lambda s: qf.length(probe, s) <= bw - 24 * S)
    art_h = 250 * S
    quote_h = (30 + 26 * len(lines) + 4) * S
    H = pad + 34 * S + art_h + 46 * S + 24 * S + 22 * S + 24 * S + 12 * S + 64 * S + 14 * S + quote_h + 14 * S + 26 * S + 16 * S + pad

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card.paste(_diag(W, H, [(0, t1), (0.38, t2), (0.62, t1), (1, t2)]), (0, 0), _round_mask((W, H), 20 * S))
    inner = Image.new("RGBA", (W - 2 * pad, H - 2 * pad), INNER)
    card.paste(inner, (pad, pad), _round_mask(inner.size, 13 * S))
    d = ImageDraw.Draw(card)
    y = pad

    # 頂欄：QQ 號 + 好感描述
    d.text((pad + 14 * S, y + 22 * S), f"QQ {qq}", font=body(12), fill=SOFT, anchor="ls")
    tf = body(13)
    rx = W - pad - 14 * S
    d.text((rx, y + 22 * S), favor_desc, font=tf, fill=t3, anchor="rs")
    cx, cy, r = rx - d.textlength(favor_desc, font=tf) - 10 * S, y + 17.5 * S, 4.5 * S
    d.ellipse((cx - r - 1.5 * S, cy - r - 1.5 * S, cx + r + 1.5 * S, cy + r + 1.5 * S), fill=t1)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=t2)
    y += 34 * S

    # 頭像
    ax, aw = pad + 10 * S, W - 2 * pad - 20 * S
    art = None
    if avatar:
        try:
            im = Image.open(BytesIO(avatar)).convert("RGB")
            k = max(aw / im.width, art_h / im.height)
            im = im.resize((int(im.width * k) + 1, int(im.height * k) + 1), Image.LANCZOS)
            art = im.crop(((im.width - aw) // 2, (im.height - art_h) // 2, (im.width - aw) // 2 + aw, (im.height - art_h) // 2 + art_h))
        except Exception:
            art = None
    if art is None:
        art = Image.new("RGB", (aw, art_h))
        ImageDraw.Draw(art)
        for yy in range(art_h):
            ImageDraw.Draw(art).line((0, yy, aw, yy), fill=_mix("#bfe6fa", "#5fa8e6", yy / art_h))
    # 底邊波浪（和麵板一樣：一排 16px 的半圓），畫在頭像圖裡面，不會超出去
    ad, rr = ImageDraw.Draw(art), 7.5 * S
    for i in range(aw // (16 * S) + 1):
        cxw = i * 16 * S + 8 * S
        ad.ellipse((cxw - rr, art_h + 1 - rr, cxw + rr, art_h + 1 + rr), fill=INNER)
    card.paste(art, (ax, y), _round_mask((aw, art_h), 10 * S))
    # 心情（太長就截，不壓到右邊的好感）
    mf = bold(12)
    px0, py0 = ax + 10 * S, y + 10 * S
    fv, ff = f"{favor:g}", hand(18)
    bwid = max(d.textlength(fv, font=ff) + 42 * S, 64 * S)
    bx1 = ax + aw - 10 * S
    mood = _ellipsize(mf, d, mood, bx1 - bwid - 8 * S - px0 - 31 * S)
    d.rounded_rectangle((px0, py0, px0 + emo.length(d, mood, mf) + 31 * S, py0 + 24 * S), radius=12 * S, fill=(251, 252, 255, 238))
    d.ellipse((px0 + 8 * S, py0 + 7.5 * S, px0 + 17 * S, py0 + 16.5 * S), fill=MOOD_DOT.get(mood_kind or mood, "#8a97b8"))  # 顏色按大類，字是她寫的詞
    emo.draw(d, (px0 + 22 * S, py0 + 17 * S), mood, mf, INK)
    # 好感
    d.rounded_rectangle((bx1 - bwid, py0, bx1, py0 + 28 * S), radius=14 * S, fill=t3)
    d.text((bx1 - bwid + 11 * S, py0 + 19.5 * S), "♡", font=bold(13), fill="#ffffff", anchor="ls")
    d.text((bx1 - bwid + 27 * S, py0 + 21 * S), fv, font=ff, fill="#ffffff", anchor="ls")
    y += art_h

    # 名字、關係
    rb = 24 * S if bot else 0  # 小機器人佔的寬
    nm = _shrink(lambda n: Mixed(hand(n), bold(n - 1)), d, name, bw - rb, 26, 18)
    shown_name = _ellipsize(nm, d, name, bw - rb)
    nm.draw(d, (bx, y + 36 * S), shown_name, INK)
    if bot:
        bot_badge(card, d, bx + nm.length(d, shown_name) + 6 * S, y + 17 * S, 22 * S, "#4f5f8c", "#e6ecf8", bot)
    y += 46 * S
    sf = Mixed(hand(13), bold(12))
    stage = _ellipsize(sf, d, short_stage(stage), bw * 0.6)
    sw = sf.length(d, stage)
    d.polygon([(bx, y), (bx + sw + 22 * S, y), (bx + sw + 14 * S, y + 12 * S), (bx + sw + 22 * S, y + 24 * S), (bx, y + 24 * S)], fill=t3)
    sf.draw(d, (bx + 10 * S, y + 17 * S), stage, "#ffffff")
    if alias:  # 緞帶右邊：〈我〉叫 TA 什麼
        af = Mixed(hand(15), bold(14))
        ax0 = bx + sw + 32 * S
        af.draw(d, (ax0, y + 18 * S), _fit(af, d, f"{me}叫你「", alias, "」", bx + bw - ax0), t3)
    y += 24 * S + 14 * S

    # 好感條：討厭 ←→ 喜歡
    bar = Image.new("RGB", (bw, 8 * S))
    bd = ImageDraw.Draw(bar)
    for xx in range(bw):
        t = xx / bw
        bd.line((xx, 0, xx, 8 * S), fill=_mix("#f3c4d1", "#eef2fa", t * 2) if t < .5 else _mix("#eef2fa", "#cfe6fb", (t - .5) * 2))
    card.paste(bar, (bx, y), _round_mask(bar.size, 4 * S))
    kx, ky = bx + bw * (max(-80, min(80, favor)) + 80) / 160, y + 4 * S  # 好感度 -80～80（主人 100，頂到最右）
    d.ellipse((kx - 7 * S, ky - 7 * S, kx + 7 * S, ky + 7 * S), fill=t3)
    d.ellipse((kx - 4 * S, ky - 4 * S, kx + 4 * S, ky + 4 * S), fill="#ffffff")
    y += 8 * S
    d.text((bx, y + 18 * S), "讨厌", font=body(11), fill=FAINT, anchor="ls")
    d.text((bx + bw, y + 18 * S), "喜欢", font=body(11), fill=FAINT, anchor="rs")
    y += 24 * S + 12 * S

    # 認識幾天 / 聊過幾次 / 一起的回憶
    def dashed(x1, y1, x2, y2):
        n = max(1, int(max(abs(x2 - x1), abs(y2 - y1)) // (6 * S)))
        for i in range(n):
            a, b = i / n, (i + 0.5) / n
            d.line((x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b), fill=RULE, width=S)
    sy0, sy1 = y, y + 64 * S
    dashed(bx, sy0, bx + bw, sy0)
    dashed(bx, sy1, bx + bw, sy1)
    cw = bw / 3
    for i, (num, lab) in enumerate(stats):
        if i:
            dashed(bx + cw * i, sy0, bx + cw * i, sy1)
        d.text((bx + cw * i + cw / 2, sy0 + 33 * S), num, font=hand(22), fill=BLUE, anchor="ms")
        d.text((bx + cw * i + cw / 2, sy0 + 53 * S), lab, font=body(12), fill=SOFT, anchor="ms")
    y = sy1 + 14 * S

    # 機器人覺得
    d.rounded_rectangle((bx, y, bx + bw, y + quote_h), radius=8 * S, fill=QUOTE)
    d.text((bx + 12 * S, y + 20 * S), f"{me}觉得", font=body(12), fill=FAINT, anchor="ls")
    if words:  # 〈我〉跟 TA 的感情，靠右一排小字
        wf = body(12)
        lab_w = d.textlength(f"{me}觉得", font=body(12)) + 20 * S
        shown = list(words)
        while shown and d.textlength(" · ".join(shown), font=wf) > bw - 24 * S - lab_w:
            shown.pop()
        if shown:
            d.text((bx + bw - 12 * S, y + 20 * S), " · ".join(shown), font=wf, fill=t3, anchor="rs")
    ly = y + 46 * S
    for i, ln in enumerate(lines):
        xx = bx + 12 * S
        units = emo.clusters(ln)
        for j, ch in enumerate(units):
            quote_mark = (i == 0 and j == 0 and ch == "「") or (i == len(lines) - 1 and j == len(units) - 1 and ch == "」")
            xx = qf.put(d, xx, ly, ch, t1 if quote_mark else INK)
        ly += 26 * S
    y += quote_h + 14 * S

    # 底欄
    try:
        fish = Image.open(FISH).convert("RGBA").resize((22 * S, 22 * S), Image.LANCZOS)
        d.ellipse((bx, y, bx + 26 * S, y + 26 * S), fill=t1)
        m = Image.new("L", fish.size, 0)
        ImageDraw.Draw(m).ellipse((0, 0, fish.width - 1, fish.height - 1), fill=255)
        card.paste(fish, (bx + 2 * S, y + 2 * S), m)
    except Exception:
        pass
    d.text((bx + 34 * S, y + 18 * S), f"认识于 {met}", font=body(12), fill=FAINT, anchor="ls")
    d.text((bx + bw, y + 18 * S), last, font=body(12), fill=FAINT, anchor="rs")

    out = Image.new("RGB", (W + 40 * S, H + 40 * S), "#eef5fc")
    out.paste(card, (20 * S, 20 * S), card)
    out.save(path, "PNG", optimize=True)
    return path


def bot_badge(img, d, x: float, y: float, size: float, color, bg, bot):
    """名字後面的 bot 標記：bot 是廠商鍵（deepseek、openai…）而 card_assets/brands 有那家的標誌就貼標誌，不然畫小機器人"""
    f = HERE / "brands" / f"{bot}.png" if isinstance(bot, str) and bot.isalnum() else None
    if f and f.is_file():
        try:
            s = round(size)
            logo = Image.open(f).convert("RGBA").resize((s, s), Image.LANCZOS)
            mask = Image.new("L", (s, s), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=s * .28, fill=255)
            img.paste(logo, (round(x), round(y)), mask)
            return
        except Exception:
            pass
    robot(d, x, y, size, color, bg)


def robot(d, x: float, y: float, size: float, color, bg=None):
    """TA 是 bot：名字後面一個小機器人（跟面板的 i-bot 圖示同一個樣子，24 格的座標按 size 縮放）。x、y 是左上角"""
    if bg is not None:
        d.rounded_rectangle((x, y, x + size, y + size), radius=size * .28, fill=bg)
    k, ox, oy = size / 24 * .82, x + size * .09, y + size * .08
    p = lambda a, b: (ox + a * k, oy + b * k)
    w = max(1, round(2 * k))
    d.ellipse((*p(10.9, 1.5), *p(13.1, 3.7)), outline=color, width=w)
    d.line((*p(12, 3.7), *p(12, 5.5)), fill=color, width=w)
    d.rounded_rectangle((*p(4.6, 5.5), *p(19.4, 16)), radius=3.6 * k, outline=color, width=w)
    d.line([p(4.6, 9.6), p(3.2, 9.6), p(3.2, 12.6), p(4.6, 12.6)], fill=color, width=w)
    d.line([p(19.4, 9.6), p(20.8, 9.6), p(20.8, 12.6), p(19.4, 12.6)], fill=color, width=w)
    for a in (9.4, 14.6):
        d.line((*p(a, 9.7), *p(a, 11.2)), fill=color, width=w)
    d.line((*p(10.2, 13.3), *p(13.8, 13.3)), fill=color, width=w)
    d.rounded_rectangle((*p(8.8, 16), *p(15.2, 20)), radius=1.8 * k, outline=color, width=w)
    for a in (10.6, 13.4):
        d.line((*p(a, 20), *p(a, 21.4)), fill=color, width=w)


def _alias(alias: str, name: str) -> str:
    """〈我〉對 TA 的稱呼：空的、和卡上的名字一樣就不寫"""
    a = emo.head(" ".join(str(alias or "").split()), 12)
    return "" if not a or a == str(name or "").strip() else a


def _size(font, d):
    return (lambda t: font.length(d, t)) if isinstance(font, Mixed) else (lambda t: emo.length(d, t, font))


def _fit(font, d, head: str, alias: str, tail: str, width: float) -> str:
    """「頭＋稱呼＋尾」放不下就從稱呼後面截、加省略號；截到一個字還放不下就不寫（回空字串）。font 是 Mixed 或 Pillow 字體"""
    size = _size(font, d)
    if size(head + alias + tail) <= width:
        return head + alias + tail
    while len(emo.clusters(alias)) > 1:
        alias = emo.drop_last(alias)
        if size(head + alias + "…" + tail) <= width:
            return head + alias + "…" + tail
    return ""


def _ellipsize(font, d, text: str, width: float) -> str:
    """放不下就從後面截、加省略號；「🍮（布丁）」這種截括號裡的，右括號留著"""
    size = _size(font, d)
    if size(text) <= width:
        return text
    tail = "）" if text.endswith("）") and "（" in text else ""
    text = text[:-1] if tail else text
    while len(emo.clusters(text)) > 1 and size(text + "…" + tail) > width:
        text = emo.drop_last(text)
    return text.rstrip() + "…" + tail


def _shrink(make, d, text: str, width: float, big: float, small: float):
    """字號從 big 往下試到 small，挑第一個放得下的；都放不下就用 small（之後再截）"""
    n = big
    while True:
        font = make(n)
        if n <= small or _size(font, d)(text) <= width:
            return font
        n -= 1


def _clip_lines(lines: list[str], most: int, fits) -> list[str]:
    """印象最多 most 行；多出來的話最後一行截掉、補「…」」"""
    if len(lines) <= most:
        return lines
    last = lines[most - 1].rstrip("」")
    while last and not fits(last + "…」"):
        last = emo.drop_last(last)
    return lines[:most - 1] + [last + "…」"]


def _printable(text: str) -> str:
    """拿掉畫不出來的字（零寬連接符這些；有彩色表情字體時表情照樣留著，沒有就拿掉，免得畫成方框）"""
    out = []
    for u in emo.clusters(str(text or "")):
        if emo.is_emoji(u):
            out.append(u)
            continue
        for ch in u:
            if ch.isspace() or ch.isascii():
                out.append(ch if ch.isprintable() or ch == " " else " ")
            elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") or "\ufe00" <= ch <= "\ufe0f":
                continue  # 控制字元、零寬連接符、變體選擇符
            elif emo.text_ok(ch):
                out.append(ch)
    return "".join(out)


def _words(text: str) -> str:
    """只留字（表情拿掉）；裡面沒有一個字母、數字、漢字就當沒有"""
    t = " ".join("".join(u for u in emo.clusters(_printable(text)) if not emo.is_emoji(u)).split())
    return t if any(c.isalnum() for c in t) else ""


def _names(name: str, alias: str, qq: str, card: str = "") -> tuple[str, str]:
    """卡上的名字和稱呼。名字裡沒有字（整個是表情、只有標點）時，旁邊用括號加上〈我〉對 TA 的稱呼（「🍮（布丁）」），
    下面就不再另寫稱呼；稱呼裡也沒有字（也是表情、沒有稱呼）就用 TA 的群名片（card）裡的字墊上。
    名字一個字都畫不出來（整個是表情、又沒有表情字體）時只寫稱呼或群名片，都沒有寫「（表情名字）」"""
    shown = " ".join(_printable(name).split())
    a = " ".join(_printable(alias).split())
    if any(c.isalnum() for c in shown):
        return shown, _alias(a, shown)
    side = _words(a)
    rest = "" if side else a  # 稱呼裡沒字就照舊寫在「〈我〉叫你」那裡
    side = side or _words(card)
    if len(emo.clusters(side)) > 12:  # 太長的截掉，有空格就在空格那裡截
        cut = emo.head(side, 12).rstrip()
        side = (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
    if not shown:
        if side:
            return side, _alias(rest, side)
        return (a, "") if a else ("（表情名字）", "")
    if side:
        return f"{shown}（{side}）", _alias(rest, shown)
    return shown, _alias(a, shown)


def short_stage(stage: str) -> str:
    """卡上只放關係的第一段（主人那句「主人，最亲的人，……」只留「主人」）"""
    return re.split(r"[，,；;（(]", stage or "", 1)[0].strip()


# ---------- 定做的虛擬形象（comic 插件 lively_avatar 做的，10-27） ----------
def made_avatars(d: Path, meta: Path | None = None) -> dict:
    """d 是 comic 的 avatars/ 目錄（圖），meta 是 avatars_meta/（avatars.json，10-29 起和圖分開放）。
    返回 qq -> {"card": 卡面圖 Path 或 None, "sheet": 三視圖 Path, "v": 修改時間}；
    圖不在的跳過。卡面圖是定做時另外畫的半身像，沒畫成的只有三視圖（卡上從三視圖裁）"""
    import json
    d = Path(d)
    f = next((x for x in ([Path(meta) / "avatars.json"] if meta else []) + [d / "avatars.json"] if x.exists()), None)
    try:
        made = json.loads(f.read_text("utf-8")).get("made") or {} if f else {}
    except Exception:
        return {}
    out = {}
    for qq, rec in made.items():
        sheet = d / str((rec or {}).get("file") or "")
        if not str(qq).isdigit() or not sheet.is_file():
            continue
        card = d / str(rec.get("card") or "")
        card = card if rec.get("card") and card.is_file() else None
        try:
            v = int((card or sheet).stat().st_mtime)
        except OSError:
            continue
        out[str(qq)] = {"card": card, "sheet": sheet, "v": v}
    return out


def _sheet_front(im: Image.Image) -> Image.Image:
    """三視圖裡最左邊那個人（正面）：按白底找整欄都是白的縫把人切開；切不開就取左邊三分之一"""
    g = im.convert("L")
    k = 4
    sm = g.resize((max(1, g.width // k), max(1, g.height // k)))
    px = sm.load()
    ink = [any(px[x, y] < 232 for y in range(sm.height)) for x in range(sm.width)]
    runs, start = [], None
    for x, on in enumerate(ink + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if x - start >= sm.width * 0.06:
                runs.append((start, x))
            start = None
    x0, x1 = (runs[0][0] * k, runs[0][1] * k) if len(runs) >= 2 else (0, im.width // 3)
    part = g.crop((x0, 0, x1, im.height))
    rows = [y for y in range(0, part.height, 2) if part.crop((0, y, part.width, y + 1)).getextrema()[0] < 232]
    y0, y1 = (rows[0], rows[-1]) if rows else (0, im.height)
    return im.crop((max(0, x0 - 8), max(0, y0 - 8), min(im.width, x1 + 8), min(im.height, y1 + 8)))


def made_faces(rec: dict) -> tuple[bytes | None, bytes | None]:
    """定做的形象 → (卡上那塊大圖, 圓頭像)，都是 JPEG；讀不出來是 (None, None)。
    有卡面圖：大圖整張（畫卡時照舊鋪滿裁中間），頭像取上方中間的正方形（臉畫在那裡）。
    只有三視圖：取正面那個人，大圖是頭到腰放在淺色底上，頭像取頭"""
    def jpg(x: Image.Image) -> bytes:
        b = BytesIO()
        x.convert("RGB").save(b, "JPEG", quality=92)
        return b.getvalue()
    try:
        if rec.get("card"):
            with Image.open(rec["card"]) as im:
                im = im.convert("RGB")
            side = int(im.height * 0.66)
            x0 = (im.width - side) // 2
            y0 = int(im.height * 0.03)
            return jpg(im), jpg(im.crop((x0, y0, x0 + side, y0 + side)))
        with Image.open(rec["sheet"]) as im:
            fig = _sheet_front(im.convert("RGB"))
        top = fig.crop((0, 0, fig.width, int(fig.height * 0.48)))
        paper = fig.getpixel((1, 1))  # 補邊用三視圖自己的底色，看不出接縫
        art = Image.new("RGB", (int(top.height * 1.37), top.height), paper)
        art.paste(top, ((art.width - top.width) // 2, 0))
        side = min(fig.width, int(fig.height * 0.22))
        x0 = (fig.width - side) // 2
        face = Image.new("RGB", (side, side), paper)
        face.paste(fig.crop((x0, 0, x0 + side, side)), (0, 0))
        return jpg(art), jpg(face)
    except Exception:
        return None, None


# ---------- 投喂過的人的卡 ----------
def _avatar_img(avatar: bytes | None):
    if not avatar:
        return None
    try:
        return Image.open(BytesIO(avatar)).convert("RGB")
    except Exception:
        return None


def _render_feed(path: Path, tier: int, tname: str, *, qq: str, name: str, favor: float, favor_desc: str, stage: str,
                 mood: str, stats: tuple, words: list, impression: str, met: str, last: str,
                 avatar: bytes | None, me: str, art: Image.Image | None = None, alias: str = "", bot: bool | str = False) -> Path:
    """tier 0 只在有專屬背景（art）時出現：不標檔位"""
    st = (fx.OWN_DARK if fx.tone(art)["dark"] else fx.OWN) if art is not None else fx.STYLE[tier]
    pal = fx.DARK if st["dark"] else fx.LIGHT
    body = lambda n: _font(BODY_FONT, n)
    serif = lambda n, b=False: fx.serif(n, S, b, fallback=_font(BOLD_FONT if b else BODY_FONT, n))
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    qf = serif(15)
    lines = _clip_lines(_wrap_font(probe, f"「{impression or '还没有印象～'}」", qf, (W - 2 * 40 * S)), 3,
                        lambda s: emo.length(probe, s, qf) <= W - 2 * 40 * S)

    panel_top = 318 * S  # 上面這一截只放背景畫，不壓字
    ad = 64 * S
    y_name = panel_top + 47 * S
    y_sub = y_name + 23 * S
    y_alias = y_sub + 19 * S  # 〈我〉叫 TA 什麼，沒有就不佔這一行
    stats_top = panel_top + (98 + (18 if alias else 0)) * S
    stats_bot = stats_top + 52 * S
    y_words = stats_bot + 24 * S  # 〈我〉跟 TA 的感情短字，沒有就不佔這一行
    y_label = stats_bot + (26 + (22 if words else 0)) * S
    y_line = y_label + 25 * S
    y_rule = y_line + 23 * S * (len(lines) - 1) + 20 * S
    y_foot = y_rule + 22 * S
    panel_bot = y_foot + 16 * S
    H = panel_bot + 12 * S

    card = (fx.cover(art, W, H) if art is not None else fx.background(tier, W, H)).convert("RGBA")
    fx.glass(card, (12 * S, panel_top, W - 12 * S, panel_bot), 18 * S, st)
    # 字和線畫在一層透明圖上最後疊上去：直接在 RGBA 圖上畫半透明的顏色，Pillow 會整個蓋掉、不混色
    ov = Image.new("RGBA", card.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((7 * S, 7 * S, W - 7 * S - 1, H - 7 * S - 1), radius=16 * S, outline=st["line"], width=max(1, S // 2 + 1))

    # 頭像、名字、一行說明；右邊是檔位（羅馬數字＋檔名）
    px = 28 * S
    ava = fx.ring(_avatar_img(avatar), ad, st, S)
    pad = (ava.width - ad) // 2
    card.alpha_composite(ava, (px - pad, panel_top + 18 * S - pad))
    nx = px + ad + 16 * S
    mark_w = 64 * S if tier else 0
    room = W - nx - mark_w - 28 * S
    rb = 30 * S if bot else 0  # 小機器人佔的寬
    nf = _shrink(lambda n: serif(n, True), d, name, room - rb, 21, 16)
    shown = _ellipsize(nf, d, name, room - rb)
    if st.get("name"):
        fx.foil_text(card, (nx, y_name), shown, nf, st["name"], "ls")
    else:
        emo.draw(d, (nx, y_name), shown, nf, pal["ink"])
    if bot:
        bot_badge(ov, d, nx + emo.length(d, shown, nf) + 7 * S, y_name - 20 * S, 23 * S, pal["ink"],
                  (255, 255, 255, 46) if st["dark"] else (40, 60, 120, 26), bot)
    items = [x for x in (short_stage(stage), favor_desc, mood if mood and mood != "平常" else "") if x]
    subf = body(11)
    while len(items) > 1 and emo.length(d, " · ".join(items), subf) + S * len(emo.clusters(" · ".join(items))) > room:
        items.pop()  # 放不下就從後面少放一項（心情、喜不喜歡）
    fx.serif_text(ov, (nx, y_sub), " · ".join(items), subf, pal["soft"], "ls", 1 * S)
    if alias:
        alf = body(11.5)
        fx.serif_text(ov, (nx, y_alias), _fit(alf, d, f"{me}叫你「", alias, "」", W - nx - mark_w - 28 * S - 12 * S),
                      alf, st["accent"], "ls", 1 * S)
    mx = W - 30 * S
    accent = st["accent"]
    if tier:
        fx.serif_text(ov, (mx, panel_top + 44 * S), fx.ROMAN[tier], serif(22), accent, "rs")
        fx.serif_text(ov, (mx, panel_top + 64 * S), tname, serif(10.5), pal["soft"], "rs", 2 * S)

    # 認識幾天 / 聊過幾次 / 一起的回憶 / 好感
    d.line((28 * S, stats_top, W - 28 * S, stats_top), fill=pal["rule"], width=max(1, S // 2))
    cols = tuple(stats) + ((f"{favor:g}", "好感"),)
    x0, x1 = 28 * S, W - 28 * S
    cw = (x1 - x0) / len(cols)
    numf, labf = serif(19), body(10)
    for i, (num, lab) in enumerate(cols):
        cx = x0 + cw * i + cw / 2
        if i:
            d.line((x0 + cw * i, stats_top + 14 * S, x0 + cw * i, stats_bot - 8 * S), fill=pal["rule"], width=max(1, S // 2))
        d.text((cx, stats_top + 29 * S), num, font=numf, fill=pal["ink"], anchor="ms")
        fx.serif_text(ov, (cx, stats_top + 45 * S), lab, labf, pal["faint"], "ms", 2 * S)

    if words:
        wf = body(11.5)
        shown = list(words)
        while len(shown) > 1 and d.textlength(" · ".join(shown), font=wf) > W - 2 * 40 * S:
            shown.pop()
        # 金色那幾檔（四、五檔、專屬背景）用金色，其他檔的點綴色太淡，用正文的次色
        fx.serif_text(ov, (W // 2, y_words), " · ".join(shown), wf, st["accent"] if tier >= 4 or art is not None else pal["soft"], "ms", 2 * S)

    # 〈我〉覺得
    fx.serif_text(ov, (W // 2, y_label), f"{me}觉得", body(10), pal["faint"], "ms", 3 * S)
    for i, ln in enumerate(lines):
        emo.draw(d, (W // 2, y_line + 23 * S * i), ln, qf, pal["ink"], "ms")

    # 底欄
    d.line((28 * S, y_rule, W - 28 * S, y_rule), fill=pal["rule"], width=max(1, S // 2))
    d.text((28 * S, y_foot), f"QQ {qq} · 认识于 {met}", font=body(9.5), fill=pal["faint"], anchor="ls")
    d.text((W - 28 * S, y_foot), last, font=body(9.5), fill=pal["faint"], anchor="rs")

    card.alpha_composite(ov)
    outer = fx.round_mask((W, H), 22 * S)
    back = _feed_back(H, outer)
    seed = int(qq) if str(qq).isdigit() else sum(map(ord, str(qq)))
    if tier < fx.ANIMATED:  # 整張是畫，存 JPG 小得多（PNG 要 700KB 以上）
        path = path.with_suffix(".jpg")
        _feed_flat(card, outer, back).save(path, "JPEG", quality=92, subsampling=0, optimize=True)
        return path
    path = path.with_suffix(".gif")
    _feed_gif(path, card, outer, back, tier, seed, (20 * S, 30 * S, W - 20 * S, panel_top - 20 * S))
    return path


def _wrap_font(d, text: str, font, width: float) -> list[str]:
    lines, cur = [], ""
    for ch in emo.clusters(text):
        if cur and emo.length(d, cur + ch, font) > width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return _no_orphan(lines + [cur], lambda t: emo.length(d, t, font) <= width)


def _feed_back(H: int, outer: Image.Image) -> Image.Image:
    """卡片底下的淺色底＋一圈柔和的影子"""
    size = (W + 40 * S, H + 44 * S)
    bg = Image.new("RGB", size, "#eef2f7")
    sh = Image.new("L", size, 0)
    sh.paste(outer.point(lambda v: v * 60 // 255), (20 * S, 26 * S))
    bg.paste((24, 30, 60), (0, 0), sh.filter(ImageFilter.GaussianBlur(10 * S)))
    return bg


def _feed_flat(card: Image.Image, outer: Image.Image, back: Image.Image) -> Image.Image:
    out = back.copy()
    out.paste(card, (20 * S, 20 * S), outer)
    return out


def _feed_gif(path: Path, card: Image.Image, outer: Image.Image, back: Image.Image, tier: int, seed: int, art_box):
    """第四檔：一道柔光慢慢掃過，停一會兒；第五檔：一直有光點往上飄，前半掃光"""
    n, sweep_n, dur = (12, 12, 90) if tier == 4 else (16, 9, 120)
    frames, durs = [], []
    for k in range(n):
        f = card.copy()
        if tier >= 5:
            fx.motes(f, art_box, 7, seed, k / n, S)
        if k < sweep_n:
            fx.sheen(f, -0.15 + 1.3 * k / (sweep_n - 1), outer)
        frames.append(_feed_flat(f, outer, back))
        durs.append(dur)
    if tier == 4:
        frames.append(_feed_flat(card, outer, back))
        durs.append(2200)
    # 共用一套調色盤，幀之間顏色不跳；沒變的像素直接用靜止那張的點，GIF 只存變了的那塊
    sample = Image.new("RGB", (frames[0].width, frames[0].height * 2))
    sample.paste(frames[-1], (0, 0))
    sample.paste(frames[sweep_n // 2], (0, frames[0].height))
    pal = sample.quantize(255, method=Image.Quantize.MEDIANCUT)
    base = _feed_flat(card, outer, back)
    qb = base.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    q = []
    for fr in frames:
        changed = ImageChops.difference(fr, base).convert("L").point(lambda v: 255 if v > 2 else 0)
        q.append(Image.composite(fr.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG), qb, changed))
    q[0].save(path, save_all=True, append_images=q[1:], duration=durs, loop=0, disposal=1, optimize=True)
