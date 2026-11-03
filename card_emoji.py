"""印象卡上的表情符號（10-27）：名字、稱呼、心情、印象裡的 🍮 這些照樣畫出來。

彩色表情字體用 Noto Color Emoji（Google，OFL 授權，倉庫裡不帶，見 README）：放 card_assets/NotoColorEmoji.ttf，
或裝系統的 fonts-noto-color-emoji。它是點陣字，只有 109px 一種大小，所以先按 109px 畫成小圖再縮到字號貼上去。
沒有這個字體就畫不了表情，card_img 照舊把表情拿掉（見 _printable）
"""
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent / "card_assets"
STRIKE = 109  # Noto Color Emoji 只有這一種大小
CANDIDATES = (
    HERE / "NotoColorEmoji.ttf",
    Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/noto/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf"),
)
ZWJ, VS15, VS16, KEYCAP = "‍", "︎", "️", "⃣"
# 表情和中文字的墨跡中心對齊：畫出來比照字號縮放後，往下挪這麼多個字號（中文字的墨跡比基線高一點點）
SHIFT = 0.0

_state: dict = {}
_text_ok: dict = {}
_cover: dict = {}
_glyphs: dict = {}


def font():
    """彩色表情字體；沒有就是 None"""
    if "font" not in _state:
        _state["font"] = None
        for p in CANDIDATES:
            if p.exists():
                try:
                    _state["font"] = ImageFont.truetype(str(p), STRIKE)
                    break
                except Exception:
                    continue
    return _state["font"]


def use_text_font(path) -> None:
    """平常寫字用的中文字體（card_img 的思源黑體）：它畫得出來的符號照舊當字寫，畫不出來的才當表情"""
    _state["text_path"] = str(path)
    _state.pop("text", None)
    _text_ok.clear()


def _text():
    if "text" not in _state:
        try:
            f = ImageFont.truetype(_state["text_path"], 20)
            _state["text"] = (f, bytes(f.getmask("\U0010fffd")))
        except Exception:
            _state["text"] = None
    return _state["text"]


def text_ok(ch: str) -> bool:
    """平常的中文字體畫不畫得出這個字；沒設字體、讀不出來時當畫得出來"""
    if _text() is None:
        return True
    if ch not in _text_ok:
        f, none = _state["text"]
        _text_ok[ch] = bytes(f.getmask(ch)) != none
    return _text_ok[ch]


def _ri(ch: str) -> bool:  # 國旗由兩個區域指示符組成
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _mod(ch: str) -> bool:  # 膚色
    return 0x1F3FB <= ord(ch) <= 0x1F3FF


def _tag(ch: str) -> bool:  # 英格蘭、蘇格蘭旗用的標籤字元
    return 0xE0020 <= ord(ch) <= 0xE007F


def clusters(text: str) -> list[str]:
    """按看到的一個一個「字」切開：組合表情（👨‍👩‍👧、👍🏽、🇨🇳、1️⃣）算一個，截字、換行時不會被切開"""
    out: list[str] = []
    for ch in str(text or ""):
        if out and (out[-1].endswith(ZWJ) or ch == ZWJ or _mod(ch) or _tag(ch)
                    or unicodedata.category(ch) in ("Mn", "Me")  # 變體選擇符、keycap、附加符號
                    or (_ri(ch) and len(out[-1]) == 1 and _ri(out[-1]))):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def head(text: str, n: int) -> str:
    """前 n 個字（組合表情算一個）"""
    return "".join(clusters(text)[:n])


def drop_last(text: str) -> str:
    return "".join(clusters(text)[:-1])


def is_emoji(unit: str) -> bool:
    """這一個字要當彩色表情畫（unit 是 clusters 切出來的一個）"""
    if not unit or font() is None:
        return False
    base = unit[0]
    if (base.isascii() and KEYCAP not in unit) or VS15 in unit:
        return False
    special = any(c in (ZWJ, VS16, KEYCAP) or _mod(c) or _ri(c) or _tag(c) for c in unit)
    if not special and _text() is not None and text_ok(base):
        return False  # ♡ ☆ ♪ 這種中文字體有的照舊當字寫
    if unit not in _cover:
        try:
            b = font().getbbox(unit)
            _cover[unit] = b[3] > b[1]  # 表情字體裡沒有這個字：高度是 0
        except Exception:
            _cover[unit] = False
    return _cover[unit]


def has_emoji(text: str) -> bool:
    return font() is not None and any(is_emoji(u) for u in clusters(text))


def _size(font_or_px) -> float:
    return float(font_or_px) if isinstance(font_or_px, (int, float)) else float(getattr(font_or_px, "size", 20))


def advance(unit: str, px: float) -> float:
    """畫成 px 字號時佔多寬"""
    return font().getlength(unit) * px / STRIKE


def glyph(unit: str, px: float) -> tuple[Image.Image, float]:
    """縮到 px 字號的表情小圖，和圖上基線離頂邊多遠"""
    key = (unit, round(px * 4))
    if key not in _glyphs:
        f = font()
        asc, desc = f.getmetrics()
        w = max(1, int(f.getlength(unit)) + 1)
        cell = Image.new("RGBA", (w, asc + desc), (0, 0, 0, 0))
        ImageDraw.Draw(cell).text((0, asc), unit, font=f, embedded_color=True, anchor="ls")
        k = px / STRIKE
        im = cell.resize((max(1, round(cell.width * k)), max(1, round(cell.height * k))), Image.LANCZOS)
        if len(_glyphs) > 400:
            _glyphs.clear()
        _glyphs[key] = (im, asc * k - SHIFT * px)
    return _glyphs[key]


def paste(img: Image.Image, x: float, baseline: float, unit: str, px: float) -> None:
    """把表情貼到 img 上：x 是左邊，baseline 是這行字的基線"""
    g, up = glyph(unit, px)
    x0, y0 = round(x), round(baseline - up)
    cx, cy = max(0, -x0), max(0, -y0)  # 超出左上邊的部分切掉
    if cx or cy:
        g = g.crop((cx, cy, g.width, g.height))
        x0, y0 = x0 + cx, y0 + cy
    if g.width <= 0 or g.height <= 0 or x0 >= img.width or y0 >= img.height:
        return
    g = g.crop((0, 0, min(g.width, img.width - x0), min(g.height, img.height - y0)))
    if img.mode == "RGBA":
        img.alpha_composite(g, (x0, y0))
    else:
        img.paste(g, (x0, y0), g)


def length(d, text: str, font_) -> float:
    """一般字體寫這段字（裡面可能有表情）有多寬"""
    if not has_emoji(text):
        return d.textlength(text, font=font_)
    px = _size(font_)
    return sum(advance(u, px) if is_emoji(u) else d.textlength(u, font=font_) for u in clusters(text))


def draw(d, xy, text: str, font_, fill, anchor: str = "ls", tracking: float = 0) -> None:
    """一般字體寫字，表情照樣畫；anchor 的第二個字母要是 s（基線）"""
    if not tracking and not has_emoji(text):
        d.text(xy, text, font=font_, fill=fill, anchor=anchor)
        return
    units = clusters(text)
    px = _size(font_)
    widths = [advance(u, px) if is_emoji(u) else d.textlength(u, font=font_) for u in units]
    total = sum(widths) + tracking * (len(units) - 1)
    x, y = xy
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    img = getattr(d, "_image", None)
    for u, w in zip(units, widths):
        if is_emoji(u):
            if img is not None:
                paste(img, x, y, u, px)
        else:
            d.text((x, y), u, font=font_, fill=fill, anchor="l" + anchor[1])
        x += w + tracking
