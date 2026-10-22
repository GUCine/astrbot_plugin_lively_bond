"""把面板「印象卡冊」裡的那張印象卡畫成圖片，群友要印象卡時發進群裡。

照著面板的 .fcard 樣式畫（2 倍尺寸）：漸變卡框、QQ 頭像、心情、好感、名字、關係、好感條、熟悉/聊過/印象、機器人覺得、認識時間。
只畫卡片，不畫檔案（好感變化、記憶、說過的話、小本本）
"""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
    return "t-love" if favor >= 25 else "t-close" if favor >= 8 else "t-mid" if favor > -8 else "t-hate"


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
    """軟糖體缺字（繁體、生僻字、符號）時逐字換成思源黑體"""

    def __init__(self, hand, fallback):
        self.hand, self.fb = hand, fallback
        self._missing = bytes(hand.getmask("\U0010fffd"))
        self._cache: dict[str, bool] = {}

    def font_for(self, ch: str):
        if ch.isspace() or ch.isascii():
            return self.hand
        if ch not in self._cache:
            self._cache[ch] = bytes(self.hand.getmask(ch)) != self._missing
        return self.hand if self._cache[ch] else self.fb

    def length(self, d, text: str) -> float:
        return sum(d.textlength(c, font=self.font_for(c)) for c in text)

    def draw(self, d, xy, text: str, fill):
        x, y = xy
        for c in text:
            f = self.font_for(c)
            d.text((x, y), c, font=f, fill=fill, anchor="ls")
            x += d.textlength(c, font=f)


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


def _wrap(mixed: Mixed, d, text: str, width: float) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if cur and mixed.length(d, cur + ch) > width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return lines + [cur]


def render(path: Path, *, qq: str, name: str, favor: float, favor_desc: str, stage: str, mood: str,
           familiarity: float, talks: int, impressions: int, impression: str, met: str, last: str,
           avatar: bytes | None, owner: bool = False, me: str = "我") -> Path:
    t1, t2, t3 = TIERS[tier_of(favor, owner)]
    hand = lambda n: _font(HAND_FONT, n)
    body = lambda n: _font(BODY_FONT, n)
    bold = lambda n: _font(BOLD_FONT, n)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))

    pad = 9 * S
    bx = pad + 18 * S
    bw = W - 2 * bx
    qf = Mixed(hand(17), bold(16))
    lines = _wrap(qf, probe, f"「{impression or '还没有印象～'}」", bw - 24 * S)[:4]
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
    # 心情
    mf = bold(12)
    px0, py0 = ax + 10 * S, y + 10 * S
    d.rounded_rectangle((px0, py0, px0 + d.textlength(mood, font=mf) + 31 * S, py0 + 24 * S), radius=12 * S, fill=(251, 252, 255, 238))
    d.ellipse((px0 + 8 * S, py0 + 7.5 * S, px0 + 17 * S, py0 + 16.5 * S), fill=MOOD_DOT.get(mood, "#8a97b8"))
    d.text((px0 + 22 * S, py0 + 17 * S), mood, font=mf, fill=INK, anchor="ls")
    # 好感
    fv, ff = f"{favor:g}", hand(18)
    bwid = max(d.textlength(fv, font=ff) + 42 * S, 64 * S)
    bx1 = ax + aw - 10 * S
    d.rounded_rectangle((bx1 - bwid, py0, bx1, py0 + 28 * S), radius=14 * S, fill=t3)
    d.text((bx1 - bwid + 11 * S, py0 + 19.5 * S), "♡", font=bold(13), fill="#ffffff", anchor="ls")
    d.text((bx1 - bwid + 27 * S, py0 + 21 * S), fv, font=ff, fill="#ffffff", anchor="ls")
    y += art_h

    # 名字、關係
    Mixed(hand(26), bold(25)).draw(d, (bx, y + 36 * S), name[:16], INK)
    y += 46 * S
    sf = hand(13)
    sw = d.textlength(stage, font=sf)
    d.polygon([(bx, y), (bx + sw + 22 * S, y), (bx + sw + 14 * S, y + 12 * S), (bx + sw + 22 * S, y + 24 * S), (bx, y + 24 * S)], fill=t3)
    d.text((bx + 10 * S, y + 17 * S), stage, font=sf, fill="#ffffff", anchor="ls")
    y += 24 * S + 14 * S

    # 好感條：討厭 ←→ 喜歡
    bar = Image.new("RGB", (bw, 8 * S))
    bd = ImageDraw.Draw(bar)
    for xx in range(bw):
        t = xx / bw
        bd.line((xx, 0, xx, 8 * S), fill=_mix("#f3c4d1", "#eef2fa", t * 2) if t < .5 else _mix("#eef2fa", "#cfe6fb", (t - .5) * 2))
    card.paste(bar, (bx, y), _round_mask(bar.size, 4 * S))
    kx, ky = bx + bw * (max(-100, min(100, favor)) + 100) / 200, y + 4 * S
    d.ellipse((kx - 7 * S, ky - 7 * S, kx + 7 * S, ky + 7 * S), fill=t3)
    d.ellipse((kx - 4 * S, ky - 4 * S, kx + 4 * S, ky + 4 * S), fill="#ffffff")
    y += 8 * S
    d.text((bx, y + 18 * S), "讨厌", font=body(11), fill=FAINT, anchor="ls")
    d.text((bx + bw, y + 18 * S), "喜欢", font=body(11), fill=FAINT, anchor="rs")
    y += 24 * S + 12 * S

    # 熟悉 / 聊過 / 印象
    def dashed(x1, y1, x2, y2):
        n = max(1, int(max(abs(x2 - x1), abs(y2 - y1)) // (6 * S)))
        for i in range(n):
            a, b = i / n, (i + 0.5) / n
            d.line((x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b), fill=RULE, width=S)
    sy0, sy1 = y, y + 64 * S
    dashed(bx, sy0, bx + bw, sy0)
    dashed(bx, sy1, bx + bw, sy1)
    cw = bw / 3
    for i, (num, lab) in enumerate(((f"{familiarity:g}", "熟悉"), (str(talks), "聊过"), (str(impressions), "印象"))):
        if i:
            dashed(bx + cw * i, sy0, bx + cw * i, sy1)
        d.text((bx + cw * i + cw / 2, sy0 + 33 * S), num, font=hand(22), fill=BLUE, anchor="ms")
        d.text((bx + cw * i + cw / 2, sy0 + 53 * S), lab, font=body(12), fill=SOFT, anchor="ms")
    y = sy1 + 14 * S

    # 機器人覺得
    d.rounded_rectangle((bx, y, bx + bw, y + quote_h), radius=8 * S, fill=QUOTE)
    d.text((bx + 12 * S, y + 20 * S), f"{me}觉得", font=body(12), fill=FAINT, anchor="ls")
    ly = y + 46 * S
    for i, ln in enumerate(lines):
        xx = bx + 12 * S
        for j, ch in enumerate(ln):
            quote_mark = (i == 0 and j == 0 and ch == "「") or (i == len(lines) - 1 and j == len(ln) - 1 and ch == "」")
            f = qf.font_for(ch)
            d.text((xx, ly), ch, font=f, fill=t1 if quote_mark else INK, anchor="ls")
            xx += d.textlength(ch, font=f)
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
