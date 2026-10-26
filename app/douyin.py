"""抖音独立解析模块（不依赖 yt-dlp）。

思路：
1. 短链 v.douyin.com/xxx 跟随跳转，拿到作品 ID（视频 / 图集 / 精选页 modal_id 都支持）
2. 用手机 UA 请求 iesdouyin.com 分享页，从 window._ROUTER_DATA 里取作品数据
   —— 这个页面不需要 a_bogus 签名，也不需要登录
3. 视频：play_addr 的 playwm 改 play 即无水印；另按 ratio 参数构造多个清晰度候选，
   下载时逐个尝试，哪个能用用哪个
4. 图集：直接下载每张原图
5. 分享页失败且用户填了 Cookie 时，再请求网页版详情页兜底
"""
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from . import fastdl

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")

_ID_PATTERNS = [
    re.compile(r"/(?:video|note|slides|share/video|share/note|share/slides)/(\d{15,21})"),
    re.compile(r"[?&](?:modal_id|aweme_id|item_id|vid)=(\d{15,21})"),
]
_ROUTER_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", re.S)
_RENDER_RE = re.compile(r'<script id="RENDER_DATA" type="application/json">(.*?)</script>', re.S)

RATIOS = [("1080p", "1080P", "最高"), ("720p", "720P", ""), ("540p", "540P", "省流")]


class DouyinError(Exception):
    pass


def _session(proxy=None):
    s = requests.Session()
    s.trust_env = False
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return s


def find_id(url: str):
    for pat in _ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def resolve_id(url: str, s: requests.Session) -> str:
    aid = find_id(url)
    if aid:
        return aid
    # 短链：跟随跳转（只要最终地址，不下载内容）
    try:
        r = s.get(url, allow_redirects=True, timeout=15, stream=True)
        final = r.url
        r.close()
    except requests.RequestException as e:
        raise DouyinError(f"打开分享链接失败：{e.__class__.__name__}")
    aid = find_id(final) or find_id(unquote(final))
    if not aid:
        # 有些跳转把 ID 放在 query 里的 url 参数中
        q = parse_qs(urlparse(final).query)
        for v in q.values():
            aid = find_id(unquote(v[0])) if v else None
            if aid:
                break
    if not aid:
        raise DouyinError("没能从链接中识别出作品 ID，请确认是单个作品的分享链接")
    return aid


def _fetch_share_item(aid: str, s: requests.Session) -> dict:
    last_err = None
    for kind in ("video", "note"):
        try:
            r = s.get(f"https://www.iesdouyin.com/share/{kind}/{aid}/", timeout=15,
                      headers={"Referer": "https://www.douyin.com/"})
            r.raise_for_status()
        except requests.RequestException as e:
            last_err = e
            continue
        m = _ROUTER_RE.search(r.text)
        if not m:
            last_err = DouyinError("分享页结构变了（找不到 _ROUTER_DATA）")
            continue
        data = json.loads(m.group(1))
        for page in (data.get("loaderData") or {}).values():
            if not isinstance(page, dict):
                continue
            res = page.get("videoInfoRes")
            if not res:
                continue
            items = res.get("item_list") or []
            if items:
                return items[0]
            flt = res.get("filter_list") or []
            if flt:
                reason = flt[0].get("detail_msg") or flt[0].get("notice") or "作品不可见"
                raise DouyinError(f"抖音返回：{reason}")
        last_err = DouyinError("分享页里没有作品数据")
    raise DouyinError(str(last_err) if last_err else "解析失败")


def _fetch_web_item(aid: str, cookie: str, proxy=None) -> dict:
    """兜底：带 Cookie 请求网页版详情页的 SSR 数据。"""
    s = _session(proxy)
    s.headers.update({"User-Agent": DESKTOP_UA, "Cookie": cookie, "Referer": "https://www.douyin.com/"})
    r = s.get(f"https://www.douyin.com/video/{aid}", timeout=15)
    m = _RENDER_RE.search(r.text)
    if not m:
        raise DouyinError("网页版也没拿到数据（Cookie 可能过期）")
    data = json.loads(unquote(m.group(1)))

    def walk(o):
        if isinstance(o, dict):
            if "awemeId" in o and ("video" in o or "images" in o):
                return o
            for v in o.values():
                hit = walk(v)
                if hit:
                    return hit
        elif isinstance(o, list):
            for v in o:
                hit = walk(v)
                if hit:
                    return hit
        return None

    det = walk(data)
    if not det:
        raise DouyinError("网页版数据里没找到作品")
    # 网页版字段是驼峰，转换成和分享页一致的结构
    v = det.get("video") or {}
    play_list = []
    for p in (v.get("playAddr") or []):
        if p.get("src"):
            play_list.append(("https:" + p["src"]) if p["src"].startswith("//") else p["src"])
    return {
        "aweme_id": aid,
        "desc": det.get("desc", ""),
        "author": {"nickname": (det.get("authorInfo") or {}).get("nickname", "")},
        "video": {
            "play_addr": {"uri": v.get("uri", ""), "url_list": play_list},
            "cover": {"url_list": [v.get("cover") or v.get("originCover") or ""]},
            "duration": v.get("duration", 0),
            "height": v.get("height"), "width": v.get("width"),
        },
        "images": [{"url_list": [i.get("urlList", [""])[-1]]} for i in (det.get("images") or [])] or None,
    }


def _first(lst):
    return (lst or [""])[0]


def parse(url: str, cookie: str = "", proxy=None) -> dict:
    s = _session(proxy)
    aid = resolve_id(url, s)
    try:
        item = _fetch_share_item(aid, s)
    except DouyinError as e:
        if not cookie:
            raise DouyinError(f"{e}。可在设置里填写抖音 Cookie 作为兜底")
        item = _fetch_web_item(aid, cookie, proxy)

    desc = (item.get("desc") or "").strip()
    author = (item.get("author") or {}).get("nickname", "")
    video = item.get("video") or {}
    images = item.get("images") or []
    cover = _first((video.get("cover") or video.get("origin_cover") or {}).get("url_list"))
    if images and not cover:
        cover = _first(images[0].get("url_list"))

    result = {
        "engine": "douyin",
        "url": url,
        "id": aid,
        "title": desc or f"抖音作品 {aid}",
        "uploader": author,
        "duration": round((video.get("duration") or 0) / 1000) if (video.get("duration") or 0) > 1000 else (video.get("duration") or 0),
        "thumbnail": cover,
        "count": 1,
        "hint": "",
    }

    if images:
        urls = []
        for img in images:
            lst = img.get("url_list") or []
            # 优先 jpeg 原图
            pick = next((u for u in lst if "jpeg" in u or ".jpg" in u), None) or _first(lst)
            if pick:
                urls.append(pick)
        result["kind"] = "images"
        result["count"] = len(urls)
        result["qualities"] = [{"key": "images", "label": "原图", "sub": f"{len(urls)} 张", "size": 0}]
        result["_candidates"] = {"images": urls}
        return result

    play = video.get("play_addr") or {}
    uri = play.get("uri") or ""
    raw_list = [u.replace("playwm", "play") for u in (play.get("url_list") or [])]
    cands = {}
    qualities = []

    # 1) 分享页自带多码率时直接用
    for br in (video.get("bit_rate") or []):
        pa = br.get("play_addr") or {}
        lst = pa.get("url_list") or []
        if not lst:
            continue
        h = pa.get("height") or 0
        w = pa.get("width") or 0
        key = f"br_{br.get('gear_name') or len(qualities)}"
        short = min(h, w) if (h and w) else h
        label = f"{short}P" if short else (br.get("gear_name") or "清晰度")
        if key in cands:
            continue
        cands[key] = lst
        qualities.append({"key": key, "label": label, "sub": "", "size": pa.get("data_size") or 0, "_h": short})
    qualities.sort(key=lambda q: q.get("_h", 0), reverse=True)

    # 2) 否则按 ratio 构造候选
    if not qualities and uri:
        for ratio, label, sub in RATIOS:
            cands[ratio] = [
                f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio={ratio}&line=0",
                f"https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio={ratio}&line=0",
                *raw_list,
            ]
            qualities.append({"key": ratio, "label": label, "sub": sub, "size": 0})
    if not qualities and raw_list:
        cands["best"] = raw_list
        qualities.append({"key": "best", "label": "原画", "sub": "", "size": 0})
    if not qualities:
        raise DouyinError("没有找到视频地址")

    # 无论哪种都把原始地址作为最后兜底
    for k in cands:
        for u in raw_list:
            if u not in cands[k]:
                cands[k].append(u)

    for q in qualities:
        q.pop("_h", None)
    qualities.append({"key": "audio_m4a", "label": "音频", "sub": "M4A", "size": 0})
    result["kind"] = "video"
    result["qualities"] = qualities
    result["_candidates"] = cands
    return result


# ---------------- 下载 ----------------

def _safe_name(s: str, limit=60) -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s)
    s = re.sub(r"#\S+", "", s)  # 去掉话题标签，文件名更干净
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:limit].rstrip(" .") or "douyin")


def _stream(s, url, dest: Path, on_chunk, cancel_event, min_size=50_000, expect="video"):
    with s.get(url, stream=True, timeout=20, allow_redirects=True,
               headers={"Referer": "https://www.douyin.com/"}) as r:
        if r.status_code != 200:
            raise DouyinError(f"HTTP {r.status_code}")
        ctype = r.headers.get("Content-Type", "")
        if expect == "video" and not (ctype.startswith("video") or "octet-stream" in ctype):
            raise DouyinError(f"返回的不是视频（{ctype or '未知类型'}）")
        total = int(r.headers.get("Content-Length") or 0)
        part = dest.with_name(dest.name + ".part")
        done = 0
        with open(part, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if cancel_event.is_set():
                    f.close()
                    part.unlink(missing_ok=True)
                    raise DouyinError("已取消")
                f.write(chunk)
                done += len(chunk)
                on_chunk(done, total)
        if done < min_size:
            part.unlink(missing_ok=True)
            raise DouyinError("文件太小，可能是错误页")
        part.replace(dest)
        return ctype, done


def download(probe: dict, quality: str, out_dir: Path, on_update, cancel_event, proxy=None,
             ffmpeg=None, connections=16) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    s = _session(proxy)
    name = f"{_safe_name(probe['title'])} [{probe['id']}]"
    cands = probe["_candidates"]

    if probe.get("kind") == "images":
        urls = cands["images"]
        folder = out_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        for i, u in enumerate(urls, 1):
            tmp = folder / f"{i:02d}"
            ctype, _ = _stream(s, u, tmp, lambda d, t: None, cancel_event, min_size=1000, expect="image")
            ext = ".webp" if "webp" in ctype else ".png" if "png" in ctype else ".jpg"
            tmp.replace(tmp.with_suffix(ext))
            on_update({"status": "downloading", "progress": i / len(urls), "speed": 0, "eta": None,
                       "downloaded": i, "total": len(urls)})
        return str(folder)

    want_audio = quality == "audio_m4a"
    key = next(iter(cands)) if (want_audio or quality not in cands) else quality
    dest = out_dir / f"{name}.mp4"
    hdrs = {"User-Agent": MOBILE_UA, "Referer": "https://www.douyin.com/"}
    meter = fastdl.Meter()
    last = [0.0]
    errors = []
    for u in cands[key]:
        try:
            known = fastdl.probe(u, hdrs, proxy)
            total, _, ctype, _ = known
            if not (ctype.startswith("video") or "octet-stream" in ctype):
                raise DouyinError(f"返回的不是视频（{ctype or '未知类型'}）")
            if total and total < 50_000:
                raise DouyinError("文件太小，可能是错误页")

            def tick(total=total):
                now = time.time()
                if now - last[0] < 0.25:
                    return
                last[0] = now
                sp = meter.speed()
                on_update({"status": "downloading", "progress": (meter.done / total) if total else 0,
                           "speed": sp, "eta": ((total - meter.done) / sp) if (sp and total) else None,
                           "downloaded": meter.done, "total": total})

            fastdl.download(u, dest, headers=hdrs, proxy=proxy, connections=connections,
                            cancel=cancel_event, on_progress=tick, meter=meter, known=known)
            break
        except fastdl.Canceled:
            raise DouyinError("已取消")
        except (DouyinError, fastdl.FastDLError, requests.RequestException) as e:
            if cancel_event.is_set():
                raise DouyinError("已取消")
            errors.append(str(e) if not isinstance(e, requests.RequestException) else e.__class__.__name__)
            meter = fastdl.Meter()
            continue
    else:
        raise DouyinError("所有下载地址都失败了：" + "；".join(dict.fromkeys(errors)))

    if want_audio:
        if not ffmpeg:
            raise DouyinError("提取音频需要 ffmpeg，请到设置里安装")
        import subprocess
        from .netutil import NO_WINDOW
        on_update({"status": "merging", "progress": 1.0, "speed": 0, "eta": None})
        audio = dest.with_suffix(".m4a")
        r = subprocess.run([ffmpeg, "-y", "-i", str(dest), "-vn", "-c:a", "copy", str(audio)],
                           capture_output=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            r = subprocess.run([ffmpeg, "-y", "-i", str(dest), "-vn", "-c:a", "aac", "-b:a", "192k", str(audio)],
                               capture_output=True, creationflags=NO_WINDOW)
        dest.unlink(missing_ok=True)
        if r.returncode != 0:
            raise DouyinError("音频提取失败")
        return str(audio)
    return str(dest)
