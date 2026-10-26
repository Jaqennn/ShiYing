"""从链接或整段分享文本里抠出 URL 并识别平台。"""
import re
from urllib.parse import urlparse

PLATFORM_META = {
    "youtube": {"name": "YouTube", "folder": "YouTube"},
    "bilibili": {"name": "哔哩哔哩", "folder": "Bilibili"},
    "x": {"name": "X", "folder": "X"},
    "douyin": {"name": "抖音", "folder": "Douyin"},
    "other": {"name": "其他", "folder": "Other"},
    "local": {"name": "本地", "folder": "Local"},
}

_DOMAINS = {
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "bilibili": ("bilibili.com", "b23.tv", "bili2233.cn", "bilibili.tv"),
    "x": ("x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com"),
    "douyin": ("douyin.com", "iesdouyin.com"),
}

# URL 遇到空白、中文标点、引号就截断
_URL_RE = re.compile(r"https?://[^\s<>\"'，。！？、；：（）【】《》「」　]+", re.I)
# 没写 http 的裸域名，如 "youtu.be/xxx"、"b23.tv/xxx"
_BARE_RE = re.compile(
    r"(?<![\w./])((?:www\.|m\.|v\.)?(?:youtube\.com|youtu\.be|bilibili\.com|b23\.tv|x\.com|twitter\.com|douyin\.com|iesdouyin\.com)/[^\s<>\"'，。！？、；：（）【】《》「」　]+)",
    re.I,
)
_BV_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b")
_TRAIL = ".,;:!?)]}>'\"…"


def platform_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return "other"
    for plat, domains in _DOMAINS.items():
        for d in domains:
            if host == d or host.endswith("." + d):
                return plat
    return "other"


def _clean(u: str) -> str:
    u = u.strip().rstrip(_TRAIL)
    # 抖音分享文本里链接后常紧跟 "复制此链接"
    u = re.split(r"[一-鿿]", u)[0].rstrip(_TRAIL)
    return u


def extract(text: str):
    """返回 {"url", "platform"}；找不到返回 None。优先已知平台的链接。"""
    if not text:
        return None
    text = text.strip()
    cands = [_clean(m.group(0)) for m in _URL_RE.finditer(text)]
    if not cands:
        cands = ["https://" + _clean(m.group(1)) for m in _BARE_RE.finditer(text)]
    if not cands:
        bv = _BV_RE.search(text)
        if bv:
            return {"url": f"https://www.bilibili.com/video/{bv.group(1)}", "platform": "bilibili"}
        return None
    for u in cands:
        p = platform_of(u)
        if p != "other":
            return {"url": normalize(u, p), "platform": p}
    return {"url": cands[0], "platform": "other"}


def extract_all(text: str):
    """批量：找出文本里所有链接（换行、空格、直接连在一起都行），按出现顺序去重。"""
    if not text:
        return []
    found = []  # (位置, url)
    for m in _URL_RE.finditer(text):
        # 两个链接首尾相连时（如 "...abchttps://..."）拆开
        offset = m.start()
        for piece in re.split(r"(?=https?://)", m.group(0)):
            if piece:
                found.append((offset, _clean(piece)))
            offset += len(piece)
    for m in _BARE_RE.finditer(text):
        found.append((m.start(1), "https://" + _clean(m.group(1))))
    joined = " ".join(u for _, u in found)
    for m in _BV_RE.finditer(text):
        if m.group(1) not in joined:
            found.append((m.start(), f"https://www.bilibili.com/video/{m.group(1)}"))
    out, seen = [], set()
    for _, u in sorted(found, key=lambda x: x[0]):
        if not re.match(r"https?://[^/\s]+\.[^/\s]+", u):
            continue
        p = platform_of(u)
        u = normalize(u, p)
        key = u.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append({"url": u, "platform": p})
    return out


def normalize(url: str, platform: str) -> str:
    if platform == "x":
        # fxtwitter / vxtwitter 等镜像改回 x.com
        url = re.sub(r"^https?://(?:www\.)?(?:fxtwitter|vxtwitter|fixupx|twitter)\.com", "https://x.com", url, flags=re.I)
    if platform == "youtube":
        # music.youtube / m.youtube 交给 yt-dlp 处理即可；去掉播放列表参数之外不改动
        pass
    return url
