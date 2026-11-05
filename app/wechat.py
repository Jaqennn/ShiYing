"""微信公众号文章视频解析模块（不依赖 yt-dlp）。

思路：
1. 用电脑浏览器 UA 打开文章页（mp.weixin.qq.com/s/xxx 或 /s?__biz=...），不需要登录
2. 页面脚本里的 video_page_infos → mp_video_trans_info 列出了每个视频的各档直链：
   format_id 100xx 是 H.264，101xx 是 H.265；1080P 一般是 10002
3. 按直链的文件名把同一个视频的各档归到一起，默认选最高清晰度；兼容优先时只用 H.264
4. 直链带时效签名，下载失败（多半是过期）时重新打开文章拿新地址再试一次
5. 视频号视频是加密的，下载不了，单独提示；老文章里嵌的腾讯视频交给 yt-dlp
"""
import html as htmlmod
import re
import subprocess
import time
from pathlib import Path

import requests

from . import fastdl

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
REFERER = "https://mp.weixin.qq.com/"

_FIELD_RE = re.compile(
    r"""["']?(\w+)["']?\s*:\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|(-?\d+(?:\.\d+)?))""")
_STEM_RE = re.compile(r"/([^/?#]+?)\.f(\d+)\.mp4", re.I)
_VID_ENTRY_RE = re.compile(r"""["']?video_id["']?\s*:\s*["'](wxv_\w+)["']""")
_MPVID_RE = re.compile(r"""data-mpvid=(?:"|\\x22|\\")(wxv_\w+)""")
_VQQ_RE = re.compile(r"""v\.qq\.com/(?:iframe/(?:preview|player)\.html\?[^"'\s<>\\]*?\bvid=|x/page/)(\w{6,20})""")
_SNAP_RE = re.compile(r"<mp-common-videosnap\b[^>]*>", re.I)
_MSG_TITLE_RE = re.compile(r"""class=["'][^"']*weui-msg__title[^"']*["'][^>]*>\s*([^<]+?)\s*<""")
_MSG_DESC_RE = re.compile(r"""class=["'][^"']*weui-msg__desc[^"']*["'][^>]*>\s*([^<]+?)\s*<""")

# 视频状态标记 → 给用户看的说明
_FLAGS = [
    ("is_mp_video_delete", "视频已被作者删除"),
    ("is_mp_video_forbid", "视频因违规无法播放"),
    ("is_mp_video_check_fail", "视频没有通过审核"),
    ("is_mp_video_checking", "视频还在审核中，过一会儿再试"),
    ("is_mp_video_transing", "视频还在转码，过一会儿再试"),
    ("mp_video_overseas_limit", "这个视频不允许在海外播放（如果开了代理，到设置里把「公众号」的代理勾掉）"),
]


class WechatError(Exception):
    pass


class TencentVideo(Exception):
    """文章里只有腾讯视频（v.qq.com）：交给 yt-dlp。"""

    def __init__(self, urls):
        super().__init__("腾讯视频")
        self.urls = urls


# ---------------- 解析 ----------------

def _session(proxy=None):
    s = requests.Session()
    s.trust_env = False
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": DESKTOP_UA, "Accept-Language": "zh-CN,zh;q=0.9",
                      "Referer": REFERER})
    return s


def _unescape(s: str) -> str:
    """页面里的字符串既有 JS 转义（\\x26、\\u4e2d、\\/）又有 HTML 转义（&amp;）。"""
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\(.)", r"\1", s)
    for _ in range(2):  # 有时是 &amp;amp; 这种二次转义
        s = htmlmod.unescape(s)
    return s


def _fields(obj_text: str) -> dict:
    out = {}
    for m in _FIELD_RE.finditer(obj_text):
        key = m.group(1)
        if key in out:
            continue
        if m.group(4) is not None:
            out[key] = m.group(4)
        else:
            out[key] = _unescape(m.group(2) if m.group(2) is not None else m.group(3))
    return out


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _flat_objects(page: str, marker: str):
    """找出包含 marker 的「没有嵌套花括号」的对象文本，比如每一档清晰度的 {format_id, width, url…}。"""
    pos = 0
    while True:
        i = page.find(marker, pos)
        if i < 0:
            return
        a = page.rfind("{", 0, i)
        b = page.find("}", i)
        pos = i + len(marker)
        if a < 0 or b < 0 or "}" in page[a:i] or (b - a) > 4000:
            continue
        yield a, page[a:b + 1]
        pos = max(pos, b)


def _collect_formats(page: str):
    """返回 [(位置, stem, format)]，同一个视频的各档 stem 相同。"""
    found = []
    seen = set()
    for pos, obj in _flat_objects(page, "format_id"):
        f = _fields(obj)
        url = (f.get("url") or "").strip()
        if ".mp4" not in url or not url.startswith("http"):
            continue
        m = _STEM_RE.search(url)
        stem = m.group(1) if m else url.split("?")[0]
        fid = str(f.get("format_id") or (m.group(2) if m else "0"))
        if (stem, fid) in seen:
            continue
        seen.add((stem, fid))
        w, h = _int(f.get("width")), _int(f.get("height"))
        found.append((pos, stem, {
            "format_id": fid,
            "width": w, "height": h,
            "short": min(w, h) if (w and h) else (h or w),
            "size": _int(f.get("filesize")),
            "duration": _int(f.get("duration")) or round(_int(f.get("duration_ms")) / 1000),
            "hevc": fid.startswith("101") or "hevc" in (f.get("video_quality_wording") or "").lower(),
            "url": url,
        }))
    return found


def _entries(page: str):
    """video_page_infos 里每个视频的那一段：video_id → (起止位置, 状态标记, 封面)。"""
    marks = list(_VID_ENTRY_RE.finditer(page))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(page), m.end() + 30000)
        seg = page[m.start():end]
        flags = {}
        for key, _ in _FLAGS:
            fm = re.search(r"""["']?%s["']?\s*:\s*["']?(\d+)""" % key, seg)
            flags[key] = bool(fm and fm.group(1) != "0")
        cm = re.search(r"""["']?cover_url["']?\s*:\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""", seg)
        cover = _unescape(cm.group(1) or cm.group(2) or "") if cm else ""
        out.append({"vid": m.group(1), "start": m.start(), "end": end, "flags": flags, "cover": cover})
    return out


def _meta(page: str):
    def first(*pats):
        for p in pats:
            m = re.search(p, page)
            if m and m.group(1).strip():
                return _unescape(m.group(1)).strip()
        return ""

    title = first(r"""msg_title\s*=\s*'((?:[^'\\]|\\.)*)'""",
                  r"""<meta\s+property="og:title"\s+content="([^"]*)\"""",
                  r"""<h1[^>]*class="[^"]*rich_media_title[^"]*"[^>]*>\s*([^<]+?)\s*<""",
                  r"""msg_title["']?\s*:\s*["']([^"']+)""")
    nickname = first(r"""var\s+nickname\s*=\s*htmlDecode\("((?:[^"\\]|\\.)*)"\)""",
                     r"""var\s+nickname\s*=\s*"((?:[^"\\]|\\.)*)\"""",
                     r"""nick_name["']?\s*:\s*["']([^"']+)""")
    cover = first(r"""var\s+msg_cdn_url\s*=\s*"([^"]+)\"""",
                  r"""<meta\s+property="og:image"\s+content="([^"]+)\"""")
    return title, nickname, cover


def _https(u: str) -> str:
    # 封面图走 https，避免界面里的混合内容问题；视频直链保持原样（mpvideo 的 https 不稳定）
    return "https://" + u[len("http://"):] if u.startswith("http://") else u


def _short_id(url: str) -> str:
    m = re.search(r"/s/([\w-]{8,})", url)
    if m:
        return m.group(1)
    mid = re.search(r"[?&]mid=(\d+)", url)
    idx = re.search(r"[?&]idx=(\d+)", url)
    if mid:
        return mid.group(1) + (f"_{idx.group(1)}" if idx else "")
    return "wechat"


def _usable(formats, compat):
    h264 = [f for f in formats if not f["hevc"]]
    return h264 if (compat and h264) else formats


def pick_format(video, tier, compat=True):
    """在不超过 tier（短边像素）的档里选最好的；都超过就选最小的那档。"""
    fs = _usable(video["formats"], compat)
    ok = [f for f in fs if f["short"] <= tier]
    if ok:
        return max(ok, key=lambda f: (f["short"], not f["hevc"], f["size"]))
    return min(fs, key=lambda f: (f["short"], f["size"]))


def parse_html(page: str, url: str, compat: bool = True) -> dict:
    """离线解析文章 HTML（方便测试）。"""
    title, nickname, page_cover = _meta(page)
    formats = _collect_formats(page)
    entries = _entries(page)
    content_vids = list(dict.fromkeys(_MPVID_RE.findall(page)))
    snaps = len(_SNAP_RE.findall(page))
    vqq = list(dict.fromkeys(_VQQ_RE.findall(page)))

    # 按 stem 分组；每组对应 video_page_infos 里的哪个 video_id 看位置落在哪一段
    videos = {}
    for pos, stem, f in formats:
        v = videos.get(stem)
        if v is None:
            ent = next((e for e in entries if e["start"] <= pos < e["end"]), None)
            v = videos[stem] = {"stem": stem, "vid": ent["vid"] if ent else "",
                                "cover": ent["cover"] if ent else "", "formats": [], "pos": pos}
        v["formats"].append(f)
    # 有的页面结构里直链旁边没有 video_id：按出现顺序对上正文里的视频
    spare = [vid for vid in content_vids if vid not in {v["vid"] for v in videos.values()}]
    for v in sorted(videos.values(), key=lambda x: x["pos"]):
        if not v["vid"] and spare:
            v["vid"] = spare.pop(0)
    vids_with_url = {v["vid"] for v in videos.values() if v["vid"]}

    # 文章正文里的顺序（data-mpvid）优先，其次是在页面里出现的顺序
    order = {vid: i for i, vid in enumerate(content_vids)}
    vlist = sorted(videos.values(), key=lambda v: (order.get(v["vid"], 10**6), v["pos"]))
    # 同一个视频在页面里偶尔会以不同 stem 出现两次：按 video_id 去重
    uniq, seen_vid = [], set()
    for v in vlist:
        if v["vid"] and v["vid"] in seen_vid:
            continue
        seen_vid.add(v["vid"])
        uniq.append(v)
    vlist = uniq

    # 正文里有、但拿不到地址的视频：看状态标记给出原因
    missing_reasons = []
    for vid in content_vids:
        if vid in vids_with_url:
            continue
        ent = next((e for e in entries if e["vid"] == vid), None)
        reason = next((txt for key, txt in _FLAGS if ent and ent["flags"].get(key)), "")
        missing_reasons.append(reason or "需要在微信里打开才能看")

    if not vlist:
        msg = _MSG_TITLE_RE.search(page)
        if msg and not formats:
            t = _unescape(msg.group(1))
            if "环境异常" in t or "验证" in page[msg.end():msg.end() + 2000]:
                raise WechatError("微信提示访问太频繁（环境异常），等几分钟再试")
            desc = _MSG_DESC_RE.search(page)
            d = _unescape(desc.group(1)).rstrip("，,。查看 ") if desc else ""
            raise WechatError(f"微信提示：{t}" + (f"（{d}）" if d and d not in t else ""))
        if missing_reasons:
            raise WechatError(missing_reasons[0] if len(set(missing_reasons)) == 1
                              else "；".join(dict.fromkeys(missing_reasons)))
        if vqq:
            raise TencentVideo([f"https://v.qq.com/x/page/{v}.html" for v in vqq])
        if snaps:
            raise WechatError("这篇文章里的是视频号视频。视频号视频是加密的，拾影暂时下载不了")
        if "js_content" not in page and "rich_media" not in page:
            raise WechatError("没打开文章页面，链接可能不完整，或者需要在微信里打开")
        raise WechatError("这篇文章里没有找到视频")

    # 清晰度档位：所有视频可用档的短边，从高到低
    tiers = sorted({f["short"] for v in vlist for f in _usable(v["formats"], compat)}, reverse=True)
    qualities = []
    for t in tiers:
        picks = [pick_format(v, t, compat) for v in vlist]
        sub = "H.265" if all(p["hevc"] for p in picks) else ""
        qualities.append({"key": str(t), "label": f"{t}P", "sub": sub,
                          "size": sum(p["size"] for p in picks)})
    if qualities and len(vlist) == 1:
        qualities[0]["sub"] = (qualities[0]["sub"] + " 最高").strip()
    qualities.append({"key": "audio_m4a", "label": "音频", "sub": "M4A", "size": 0})

    best = pick_format(vlist[0], tiers[0], compat)
    hints = []
    if missing_reasons:
        hints.append(f"另有 {len(missing_reasons)} 个视频拿不到：" + "；".join(dict.fromkeys(missing_reasons)))
    if snaps:
        hints.append(f"另有 {snaps} 个视频号视频（加密的，下载不了）")
    if vqq:
        hints.append(f"另有 {len(vqq)} 个腾讯视频，这次只下载公众号视频")

    return {
        "engine": "wechat",
        "url": url,
        "id": vlist[0]["vid"] if len(vlist) == 1 and vlist[0]["vid"] else _short_id(url),
        "title": title or "公众号视频",
        "uploader": nickname,
        "duration": sum(max((f["duration"] for f in v["formats"]), default=0) for v in vlist),
        "thumbnail": _https(vlist[0]["cover"] or page_cover),
        "vertical": bool(best["height"] > best["width"]),
        "count": len(vlist),
        "kind": "videos" if len(vlist) > 1 else "video",
        "hint": "；".join(hints),
        "qualities": qualities,
        "_videos": [{"vid": v["vid"], "stem": v["stem"], "formats": v["formats"]} for v in vlist],
        "_compat": compat,
        "_tiers": tiers,
    }


def fetch_page(url: str, proxy=None) -> str:
    s = _session(proxy)
    try:
        r = s.get(url, timeout=20)
    except requests.RequestException as e:
        raise WechatError(f"打开文章失败：{e.__class__.__name__}，检查一下网络")
    if r.status_code != 200:
        raise WechatError(f"打开文章失败：HTTP {r.status_code}")
    r.encoding = "utf-8"
    return r.text


def parse(url: str, proxy=None, compat: bool = True) -> dict:
    return parse_html(fetch_page(url, proxy), url, compat)


# ---------------- 下载 ----------------

def _safe_name(s: str, limit=60) -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].rstrip(" .") or "wechat"


def _fresh_url(probe, stem, fid, proxy):
    """直链过期时重新打开文章，找同一个视频同一档的新地址。"""
    page = fetch_page(probe["url"], proxy)
    for _, s, f in _collect_formats(page):
        if s == stem and f["format_id"] == fid:
            return f["url"]
    return None


def _extract_audio(ffmpeg, src: Path) -> Path:
    from .netutil import NO_WINDOW
    audio = src.with_suffix(".m4a")
    r = subprocess.run([ffmpeg, "-y", "-i", str(src), "-vn", "-c:a", "copy", str(audio)],
                       capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        r = subprocess.run([ffmpeg, "-y", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k", str(audio)],
                           capture_output=True, creationflags=NO_WINDOW)
    src.unlink(missing_ok=True)
    if r.returncode != 0:
        raise WechatError("音频提取失败")
    return audio


def download(probe: dict, quality: str, out_dir: Path, on_update, cancel_event, proxy=None,
             ffmpeg=None, connections=16) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    want_audio = quality == "audio_m4a"
    if want_audio and not ffmpeg:
        raise WechatError("提取音频需要 ffmpeg，请到设置里安装")
    compat = probe.get("_compat", True)
    tier = probe["_tiers"][0] if (want_audio or not str(quality).isdigit()) else int(quality)
    picks = [(v, pick_format(v, tier, compat)) for v in probe["_videos"]]
    grand = sum(f["size"] for _, f in picks)
    title = _safe_name(probe["title"])
    multi = len(picks) > 1
    folder = out_dir / f"{title} [{probe['id']}]" if multi else out_dir
    folder.mkdir(parents=True, exist_ok=True)

    hdrs = {"User-Agent": DESKTOP_UA, "Referer": REFERER}
    meter = fastdl.Meter()
    last = [0.0]
    results = []

    for i, (v, f) in enumerate(picks, 1):
        dest = folder / (f"{i:02d}.mp4" if multi else f"{title} [{v['vid'] or probe['id']}].mp4")
        base = meter.done
        url = f["url"]
        refreshed = False
        while True:
            try:
                known = fastdl.probe(url, hdrs, proxy)
                total, _, ctype, _ = known
                if ctype and not (ctype.startswith("video") or "octet-stream" in ctype):
                    raise WechatError(f"返回的不是视频（{ctype}）")
                todo = max(grand, base + total)

                def tick(todo=todo):
                    now = time.time()
                    if now - last[0] < 0.25:
                        return
                    last[0] = now
                    sp = meter.speed()
                    on_update({"status": "downloading", "progress": (meter.done / todo) if todo else 0,
                               "speed": sp, "eta": ((todo - meter.done) / sp) if (sp and todo) else None,
                               "downloaded": meter.done, "total": todo, "conns": meter.conns})

                fastdl.download(url, dest, headers=hdrs, proxy=proxy, connections=connections,
                                cancel=cancel_event, on_progress=tick, meter=meter, known=known)
                break
            except fastdl.Canceled:
                raise WechatError("已取消")
            except (WechatError, fastdl.FastDLError, requests.RequestException) as e:
                if cancel_event.is_set():
                    raise WechatError("已取消")
                with meter.lock:
                    meter.done = base
                if refreshed:
                    msg = str(e) if not isinstance(e, requests.RequestException) else e.__class__.__name__
                    raise WechatError(f"下载失败：{msg}")
                # 多半是直链过期了：重新打开文章拿新地址
                refreshed = True
                try:
                    new = _fresh_url(probe, v["stem"], f["format_id"], proxy)
                except WechatError:
                    new = None
                if not new:
                    msg = str(e) if not isinstance(e, requests.RequestException) else e.__class__.__name__
                    raise WechatError(f"下载失败：{msg}")
                url = new

        if want_audio:
            on_update({"status": "merging", "progress": 1.0, "speed": 0, "eta": None})
            dest = _extract_audio(ffmpeg, dest)
        results.append(dest)

    return str(folder) if multi else str(results[0])
