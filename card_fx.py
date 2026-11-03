"""投喂過的人的印象卡（10-26）：整張是一幅小肥魚的背景畫，下半一塊霧面玻璃放資料，中間圓形頭像，名字用襯線體。

五檔不靠堆特效分高低，靠背景和材質（上半只放畫，不壓字；資料都在玻璃上）：
1 清晨浅滩・象牙白圈
2 月下海面・深色玻璃、銀圈
3 浅海光束・珍珠圈
4 黄昏金海・香檳金圈、金色細框、金字名字，卡上慢慢掃過一道光（動圖）
5 深海星河・深色玻璃、金圈金字，掃光，背景裡幾點光慢慢往上飄（動圖）

背景是生圖畫的（card_assets/feed/bg1～5.webp，用漫畫插件那套生圖設定＋小肥魚參考圖），可以換成自己的；
沒有這張圖就用這一檔的漸變代替。檔位的金額和名字在 bond 設定 feed_tiers，這裡只按第幾檔畫。

專屬背景（own_bgs）：某個人單獨放一張直式的畫（bond 資料目錄 card_bg/<QQ>.webp|png|jpg），TA 的卡就用這張，
材質是香檳金（OWN），玻璃亮暗看畫的下半截；投喂過的照樣標檔位、四檔起照樣掃光。
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import card_emoji as emo

HERE = Path(__file__).parent / "card_assets"
BG_DIR = HERE / "feed"
ANIMATED = 4  # 這一檔起發動圖（QQ 裡 GIF 會動）
MAX_TIER = 5
ROMAN = ("", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ")
LOOK = {  # 發完卡告訴〈我〉這張長什麼樣（給模型看，簡體）
    1: "背景是清晨浅滩上的本鱼，象牙白的头像圈",
    2: "背景是月光下的海面，深色玻璃、银色的头像圈",
    3: "背景是浅海里的光束，珍珠色的头像圈",
    4: "背景是黄昏的金色大海，香槟金的头像圈和细框、金字名字，卡上会慢慢扫过一道光（动图）",
    5: "背景是深海星河，深色玻璃配金色，卡上会扫过一道光、背景里有光点往上飘（动图）",
}

# 每一檔：頭像圈的材質、細框、名字、玻璃（亮／暗）、沒背景圖時的漸變
STYLE = {
    1: {"ring": ["#fffdf7", "#e9dcc0", "#fffaf0", "#d8c6a0"], "line": (255, 255, 255, 150), "dark": False,
        "accent": "#b89a5e", "fallback": ["#f7e9d4", "#cfe6ea", "#eef3f2"]},
    2: {"ring": ["#f7f9fc", "#a9b3c4", "#eef2f7", "#8b96aa"], "line": (220, 228, 240, 150), "dark": True,
        "accent": "#d5dbe6", "fallback": ["#3b4a6b", "#8ea0bd", "#dfe5ee"]},
    3: {"ring": ["#fbf6ff", "#d7e9f6", "#f8e7f0", "#dcefe9", "#c9cfdc"], "line": (255, 255, 255, 160), "dark": False,
        "accent": "#8fa6c9", "fallback": ["#7fc4d8", "#cde8ee", "#eef6f7"]},
    4: {"ring": ["#fbefd2", "#c49a50", "#fff5dc", "#a37a35"], "line": (226, 194, 128, 210), "dark": False,
        "accent": "#b48a3e", "name": ["#7a5a22", "#b08a45", "#6e501c"], "fallback": ["#e7b56c", "#f3dcb0", "#f7efe1"]},
    5: {"ring": ["#fff1cf", "#d2a85a", "#fff8e6", "#b0853b"], "line": (226, 194, 128, 220), "dark": True,
        "accent": "#e6c27a", "name": ["#fff4d6", "#e6c27a", "#fbe6b3"], "fallback": ["#0b1030", "#1c2a63", "#101633"]},
}
OWN = {"ring": STYLE[4]["ring"], "line": STYLE[4]["line"], "dark": False, "accent": "#a8803a",
       "name": STYLE[4]["name"], "fallback": STYLE[4]["fallback"]}  # 專屬背景：香檳金；深色玻璃時換成第五檔的金
OWN_DARK = {**OWN, "dark": True, "accent": STYLE[5]["accent"], "name": STYLE[5]["name"]}
LIGHT = {"ink": "#1f2540", "soft": "#5f6886", "faint": "#949bb3", "rule": (31, 37, 64, 34), "glass": (255, 255, 255, 178)}
DARK = {"ink": "#f3f1ea", "soft": "#c3c7d8", "faint": "#8d94b3", "rule": (255, 255, 255, 40), "glass": (12, 17, 44, 150)}


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, k: float):
    return tuple(int(p + (q - p) * k) for p, q in zip(_rgb(a), _rgb(b)))


def gradient(w: int, h: int, stops: list[str], angle: float = 0.0) -> Image.Image:
    """等距多段漸變：angle 0 是由上到下，正值往右斜（先畫小圖再放大）"""
    sw, sh = max(2, w // 6), max(2, h // 6)
    g = Image.new("RGB", (sw, sh))
    px = g.load()
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    n = len(stops) - 1
    for y in range(sh):
        for x in range(sw):
            t = (y / (sh - 1)) * ca + (x / (sw - 1)) * sa
            t = min(1.0, max(0.0, t / (abs(ca) + abs(sa))))
            i = min(n - 1, int(t * n))
            px[x, y] = _mix(stops[i], stops[i + 1], t * n - i)
    return g.resize((w, h), Image.BICUBIC)


def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    """按卡片大小裁滿（cover，偏上對齊：要緊的東西都在上半）"""
    im = im.convert("RGB")
    k = max(w / im.width, h / im.height)
    im = im.resize((math.ceil(im.width * k), math.ceil(im.height * k)), Image.LANCZOS)
    x0 = (im.width - w) // 2
    return im.crop((x0, 0, x0 + w, h))


def background(tier: int, w: int, h: int) -> Image.Image:
    """這一檔的背景畫，按卡片大小裁滿"""
    for ext in ("webp", "png", "jpg"):
        p = BG_DIR / f"bg{tier}.{ext}"
        if p.exists():
            try:
                return cover(Image.open(p), w, h)
            except Exception:
                break
    return gradient(w, h, STYLE[tier]["fallback"], 20)


def tone(im: Image.Image) -> dict:
    """畫的下半截（玻璃底下那塊）偏暗就配深色玻璃；deep 是畫最底下的顏色（面板的卡比畫長時拿來墊底）"""
    im = im.convert("RGB")
    w, h = im.size
    low = im.crop((0, int(h * 0.45), w, int(h * 0.95))).convert("L").resize((48, 48), Image.BOX)
    lum = sum(low.getdata()) / (48 * 48)
    r, g, b = im.crop((0, int(h * 0.8), w, h)).resize((1, 1), Image.BOX).getpixel((0, 0))
    return {"dark": lum < 140, "deep": f"#{r:02x}{g:02x}{b:02x}"}


_TONE: dict = {}


def own_bgs(d: Path) -> dict:
    """專屬背景：d/<QQ>.webp|png|jpg，旁邊可以放 <QQ>.txt 一句說這張畫的是什麼（給〈我〉看，可以用〈我〉〈主人〉）。
    返回 qq -> {path, v（修改時間）, dark, deep, look}；讀不出來的跳過"""
    out = {}
    try:
        files = sorted(Path(d).iterdir())
    except OSError:
        return out
    for p in files:
        if p.suffix.lower() not in (".webp", ".png", ".jpg", ".jpeg") or not p.stem.isdigit() or p.stem in out:
            continue
        try:
            mt = p.stat().st_mtime
            if (str(p), mt) not in _TONE:
                with Image.open(p) as im:
                    _TONE[(str(p), mt)] = tone(im)
            t = p.with_suffix(".txt")
            look = next((x.strip() for x in t.read_text("utf-8-sig").splitlines() if x.strip()), "")[:120] if t.exists() else ""
            out[p.stem] = {"path": p, "v": int(mt), **_TONE[(str(p), mt)], "look": look}
        except Exception:
            continue
    return out


def round_mask(size, r: float) -> Image.Image:
    k = 3  # 圓角先放大畫再縮，邊緣不鋸齒
    m = Image.new("L", (size[0] * k, size[1] * k), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] * k - 1, size[1] * k - 1), radius=r * k, fill=255)
    return m.resize(size, Image.LANCZOS)


def circle_mask(d: int) -> Image.Image:
    k = 4
    m = Image.new("L", (d * k, d * k), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d * k - 1, d * k - 1), fill=255)
    return m.resize((d, d), Image.LANCZOS)


def glass(card: Image.Image, box, r: float, st: dict):
    """霧面玻璃：底下的畫糊開、蓋一層半透明，頂邊一條亮線（st 是 STYLE 裡的一檔或 OWN）"""
    x0, y0, x1, y1 = (int(v) for v in box)
    pal = DARK if st["dark"] else LIGHT
    region = card.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(26))
    tint = Image.new("RGBA", region.size, pal["glass"])
    region = Image.alpha_composite(region.convert("RGBA"), tint)
    m = round_mask(region.size, r)
    card.paste(region, (x0, y0), m)
    edge = (255, 255, 255, 70) if st["dark"] else (255, 255, 255, 200)
    ov = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=r, outline=edge, width=1)
    card.alpha_composite(ov)


def ring(avatar: Image.Image | None, d: int, st: dict, S: int) -> Image.Image:
    """圓形頭像加一圈材質（銀、珍珠、金……），外面再一圈很淡的影子"""
    pad = 10 * S
    band = int(3.2 * S)
    out = Image.new("RGBA", (d + 2 * pad, d + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("L", out.size, 0)
    ImageDraw.Draw(sh).ellipse((pad, pad + 3 * S, pad + d, pad + d + 3 * S), fill=70)
    out.paste((10, 14, 40, 255), (0, 0), sh.filter(ImageFilter.GaussianBlur(6 * S)))
    metal = gradient(d, d, st["ring"], 35).convert("RGBA")
    out.paste(metal, (pad, pad), circle_mask(d))
    inner = d - 2 * band
    if avatar is None:
        avatar = gradient(inner, inner, ["#cfe4f5", "#8fb4dc"], 0)
    a = avatar.convert("RGB")
    k = max(inner / a.width, inner / a.height)
    a = a.resize((math.ceil(a.width * k), math.ceil(a.height * k)), Image.LANCZOS)
    a = a.crop(((a.width - inner) // 2, (a.height - inner) // 2, (a.width - inner) // 2 + inner, (a.height - inner) // 2 + inner))
    out.paste(a, (pad + band, pad + band), circle_mask(inner))
    return out


def serif_text(card: Image.Image, xy, text: str, font, fill, anchor="ms", tracking: float = 0):
    """寫字；tracking 是字距（像素），襯線字配一點字距才顯得疏朗。表情照樣畫（card_emoji）"""
    emo.draw(ImageDraw.Draw(card), xy, text, font, fill, anchor, tracking)  # card 是透明的那層（見 card_img），直接畫


def foil_text(card: Image.Image, xy, text: str, font, stops: list[str], anchor="ms"):
    """金字：用漸變填字（第四、五檔的名字）；裡面的表情照原色畫，不鍍金"""
    mask = Image.new("L", card.size, 0)
    md = ImageDraw.Draw(mask)
    faces = []
    if emo.has_emoji(text):  # 字和表情分開：字畫進遮罩，表情記下位置最後貼
        units = emo.clusters(text)
        px = getattr(font, "size", 20)
        widths = [emo.advance(u, px) if emo.is_emoji(u) else md.textlength(u, font=font) for u in units]
        x, y = xy
        x -= sum(widths) / 2 if anchor[0] == "m" else sum(widths) if anchor[0] == "r" else 0
        for u, w in zip(units, widths):
            if emo.is_emoji(u):
                faces.append((x, u))
            else:
                md.text((x, y), u, font=font, fill=255, anchor="l" + anchor[1])
            x += w
    else:
        md.text(xy, text, font=font, fill=255, anchor=anchor)
    box = mask.getbbox()
    if box:
        fill = gradient(box[2] - box[0], box[3] - box[1], stops, 0).convert("RGBA")
        layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        layer.paste(fill, box[:2])
        layer.putalpha(mask)
        card.alpha_composite(layer)
    for x, u in faces:
        emo.paste(card, x, xy[1], u, getattr(font, "size", 20))


def sheen(img: Image.Image, center: float, mask: Image.Image | None = None, width: float = 0.12, peak: float = 0.26):
    """一道斜著的柔光（center 0～1 是光在卡上的位置），只在 mask 裡"""
    w, h = img.size
    sw, sh = max(2, w // 8), max(2, h // 8)
    g = Image.new("L", (sw, sh))
    px = g.load()
    for y in range(sh):
        for x in range(sw):
            t = (x / sw * 0.6 + y / sh * 0.8) / 1.4
            k = max(0.0, 1 - abs(t - center) / width)
            px[x, y] = int(255 * peak * (k * k * (3 - 2 * k)))
    m = g.resize((w, h), Image.BICUBIC)
    if mask is not None:
        m = ImageChops.multiply(m, mask)
    light = Image.new("RGBA", (w, h), (255, 250, 236, 255))
    light.putalpha(m)
    img.alpha_composite(light)


def motes(img: Image.Image, box, n: int, seed: int, phase: float, S: int):
    """第五檔背景裡幾點光慢慢往上飄（位置按 seed 固定，同一個人每次一樣）"""
    x0, y0, x1, y1 = box
    rnd = random.Random(seed)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    for _ in range(n):
        x, start, speed = x0 + rnd.random() * (x1 - x0), rnd.random(), 0.5 + rnd.random() * 0.5
        r = (1.4 + rnd.random() * 2.2) * S
        y = y1 - ((start + phase * speed) % 1.0) * (y1 - y0)
        a = int(200 * math.sin(math.pi * ((start + phase * speed) % 1.0)))  # 冒出來、飄到頂淡掉
        dot = Image.new("RGBA", (int(r * 6), int(r * 6)), (0, 0, 0, 0))
        dd = ImageDraw.Draw(dot)
        c = r * 3
        dd.ellipse((c - r * 2.4, c - r * 2.4, c + r * 2.4, c + r * 2.4), fill=(255, 226, 160, a // 4))
        dd.ellipse((c - r, c - r, c + r, c + r), fill=(255, 246, 220, a))
        dot = dot.filter(ImageFilter.GaussianBlur(r * 0.6))
        layer.alpha_composite(dot, (int(x - c), int(y - c)))
    img.alpha_composite(layer)


_SERIF_IDX: dict = {}


def serif(size: float, S: int, bold: bool = False, fallback=None):
    """襯線體（Noto Serif CJK 的簡體那一份）；系統沒有就用 fallback"""
    names = ["NotoSerifCJK-Bold.ttc", "NotoSerifCJK-Regular.ttc"] if bold else ["NotoSerifCJK-Regular.ttc"]
    dirs = ["/usr/share/fonts/opentype/noto", "/usr/share/fonts/noto-cjk", "/usr/share/fonts/google-noto-cjk", str(HERE)]
    cands = [Path(d) / n for n in names for d in dirs] + [
        HERE / ("serif-bold.otf" if bold else "serif.otf"), Path("/System/Library/Fonts/Supplemental/Songti.ttc")]
    for p in cands:
        if not p.exists():
            continue
        if p not in _SERIF_IDX:
            _SERIF_IDX[p] = 0
            for i in range(12):  # ttc 裡挑簡體中文那一份
                try:
                    f = ImageFont.truetype(str(p), 20, index=i)
                except Exception:
                    break
                fam = " ".join(f.getname())
                if " SC" in fam or "SC " in fam or "Songti SC" in fam:
                    _SERIF_IDX[p] = i
                    break
        try:
            return ImageFont.truetype(str(p), int(size * S), index=_SERIF_IDX[p])
        except Exception:
            continue
    return fallback
