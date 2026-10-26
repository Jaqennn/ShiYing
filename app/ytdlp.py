"""yt-dlp 引擎：以子进程方式调用 yt-dlp.exe，解析信息、组装清晰度、实时回报进度。

不内嵌 yt-dlp 库，是为了网站改版时在软件里一键“更新内核”即可，无需重新打包。
"""
import json
import re
import subprocess
import threading
import time
from pathlib import Path

from . import components, fastdl
from .netutil import NO_WINDOW, kill_tree, resolve_proxy

QUALITY_LABELS = {4320: "8K", 2160: "4K", 1440: "2K", 1080: "1080P", 720: "720P",
                  480: "480P", 360: "360P", 240: "240P", 144: "144P"}
_STD_HEIGHTS = sorted(QUALITY_LABELS, reverse=True)


class EngineError(Exception):
    pass


def _bucket(h: int, w: int = 0) -> int:
    """把实际高度归档到常见档位（竖屏视频按短边算）。"""
    if not h:
        return 0
    short = min(h, w) if w else h
    for std in sorted(_STD_HEIGHTS):
        if short <= std * 1.06:
            return std
    return _STD_HEIGHTS[0]


def base_args(platform: str, settings) -> list:
    ytdlp = components.find("yt-dlp")
    if not ytdlp:
        raise EngineError("缺少 yt-dlp 组件，请到「设置 → 组件」一键安装")
    args = [ytdlp, "--encoding", "utf-8", "--no-color", "--no-playlist", "--ignore-config"]
    proxy = resolve_proxy(settings, platform)
    if proxy:
        args += ["--proxy", proxy]
    ff = components.find("ffmpeg")
    if ff:
        args += ["--ffmpeg-location", str(Path(ff).parent)]
    deno = components.find("deno")
    if deno:
        args += ["--js-runtimes", f"deno:{deno}"]
    ck = settings["cookies"].get(platform) or {}
    if ck.get("mode") == "file" and ck.get("file"):
        args += ["--cookies", ck["file"]]
    elif ck.get("mode") == "browser" and ck.get("browser"):
        args += ["--cookies-from-browser", ck["browser"]]
    return args


def _run_json(args, timeout=90):
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise EngineError("解析超时：检查网络或代理设置")
    if r.returncode != 0 or not r.stdout.strip():
        raise EngineError(explain_error(r.stderr or r.stdout))
    # -J 只输出一行 JSON；前面可能夹杂警告
    for line in reversed(r.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise EngineError("解析结果为空")


def probe(url: str, platform: str, settings) -> dict:
    info = _run_json(base_args(platform, settings) + ["-J", "--", url])
    entries = None
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise EngineError("这个链接里没有可下载的视频")
    head = entries[0] if entries else info
    formats = []
    for e in (entries or [info]):
        formats += e.get("formats") or []

    # 统计每个档位能拿到的最好视频流
    by_h = {}
    audio_size = 0
    for f in formats:
        size = f.get("filesize") or f.get("filesize_approx") or 0
        vc, ac = f.get("vcodec"), f.get("acodec")
        if vc and vc != "none" and f.get("height"):
            b = _bucket(f["height"], f.get("width") or 0)
            cur = by_h.setdefault(b, {"fps": 0, "size": 0, "codecs": set()})
            cur["fps"] = max(cur["fps"], f.get("fps") or 0)
            cur["size"] = max(cur["size"], size)
            cur["codecs"].add((vc or "").split(".")[0])
        elif ac and ac != "none" and (not vc or vc == "none"):
            audio_size = max(audio_size, size)

    qualities = []
    for h in sorted(by_h, reverse=True):
        meta = by_h[h]
        qualities.append({
            "key": str(h),
            "label": QUALITY_LABELS.get(h, f"{h}P"),
            "sub": ("60帧" if meta["fps"] >= 50 else "") or "",
            "size": (meta["size"] + audio_size) if meta["size"] else 0,
        })
    if not qualities:  # 没有分辨率信息（纯 http 单文件）
        qualities.append({"key": "best", "label": "最佳", "sub": "", "size": 0})
    qualities.append({"key": "audio_m4a", "label": "音频", "sub": "M4A", "size": audio_size})
    qualities.append({"key": "audio_mp3", "label": "音频", "sub": "MP3", "size": 0})

    hint = ""
    top = max(by_h) if by_h else 0
    no_cookie = (settings["cookies"].get(platform) or {}).get("mode", "none") == "none"
    if platform == "bilibili" and no_cookie and top and top <= 1080:
        hint = f"未登录最高 {QUALITY_LABELS.get(top, f'{top}P')}。1080P 高码率、4K 需要在设置里导入 B站 Cookie（4K 需大会员）"
    if platform == "youtube" and no_cookie and top and top <= 1080:
        hint = "未登录时 YouTube 通常只给到 1080P；如果原视频有 2K/4K，在设置里导入 YouTube Cookie"
    if platform == "youtube" and not components.find("deno"):
        hint = "未安装 Deno，YouTube 可能缺少高清格式或解析失败，建议到设置里安装"

    return {
        "engine": "ytdlp",
        "site": _site_name(info, head, url),
        "url": url,
        "id": head.get("id") or info.get("id"),
        "title": info.get("title") or head.get("title") or "未命名",
        "uploader": head.get("uploader") or head.get("channel") or head.get("uploader_id") or "",
        "duration": head.get("duration") or 0,
        "thumbnail": head.get("thumbnail") or _best_thumb(head),
        "count": len(entries) if entries else 1,
        "qualities": qualities,
        "hint": hint,
    }


def _site_name(info, head, url):
    """其他网站显示真实域名（如 vimeo.com、ted.com），而不是笼统的「其他」。"""
    dom = info.get("webpage_url_domain") or head.get("webpage_url_domain")
    if not dom:
        from urllib.parse import urlparse
        dom = urlparse(head.get("webpage_url") or url).hostname or ""
    dom = dom.lower()
    for pre in ("www.", "m.", "mobile."):
        if dom.startswith(pre):
            dom = dom[len(pre):]
    return dom


def _best_thumb(info):
    thumbs = info.get("thumbnails") or []
    for t in reversed(thumbs):
        if t.get("url"):
            return t["url"]
    return ""


def format_args(quality: str, settings) -> list:
    compat = settings["compat_mode"]
    if quality == "audio_m4a":
        return ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a"]
    if quality == "audio_mp3":
        return ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
    sort = []
    if quality.isdigit():
        sort.append(f"res:{quality}")
    else:
        sort.append("res")
    if compat:
        sort += ["fps", "vcodec:h264", "acodec:m4a"]
    # 源只有 VP9/AV1 时照样合并进 mp4，不强制转码（转码太慢）
    return ["-f", "bv*+ba/b", "-S", ",".join(sort),
            "--merge-output-format", settings["container"] or "mp4"]


# ---------------- 下载 ----------------

_P = "SYP"   # 进度行前缀
_F = "SYF"   # 选中的格式
_D = "SYD"   # 最终文件
_PROGRESS_TMPL = (
    f"download:{_P}|%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(info.format_id)s"
)
_FORMATS_TMPL = (
    f"before_dl:{_F}|%(requested_formats.:.format_id)j|%(requested_formats.:.filesize)j|"
    "%(requested_formats.:.filesize_approx)j|%(format_id)s|%(filesize,filesize_approx)s"
)
_POST_RE = re.compile(r"^\[(Merger|ExtractAudio|VideoConvertor|VideoRemuxer|FixupM3u8|FixupM4a|FixupStretched|FixupDuplicateMoov|FixupTimestamp)\]")


def _num(s):
    try:
        if s in (None, "NA", "None", "null", ""):
            return None
        return float(s)
    except ValueError:
        return None


def _jlist(s):
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else None
    except Exception:
        return None


class ProgressTracker:
    """把多条流（视频 + 音频）的进度按体积加权合成一个总进度。"""

    def __init__(self):
        self.expected = {}   # format_id -> 预估字节
        self.done = {}
        self.total = {}
        self.finished = set()
        self._last = 0.0     # 分片下载时预估总量会抖动，进度只进不退

    def set_formats(self, ids, sizes):
        for i, fid in enumerate(ids):
            self.expected[str(fid)] = sizes[i] if i < len(sizes) and sizes[i] else None
        known = [v for v in self.expected.values() if v]
        if len(self.expected) == 2 and len(known) < 2:
            # 大小未知：按经验 视频85% / 音频15%
            ids_ = list(self.expected)
            base = known[0] if known else 100.0
            if self.expected[ids_[0]] is None and self.expected[ids_[1]] is None:
                self.expected[ids_[0]], self.expected[ids_[1]] = 85.0, 15.0
            elif self.expected[ids_[0]] is None:
                self.expected[ids_[0]] = base * 85 / 15
            else:
                self.expected[ids_[1]] = base * 15 / 85

    def update(self, fid, done, total):
        fid = str(fid)
        if done is not None:
            self.done[fid] = done
        if total:
            self.total[fid] = total

    def all_finished(self):
        ids = list(self.expected) or list(self.total)
        return bool(ids) and all(i in self.finished for i in ids)

    def fraction(self):
        self._last = max(self._last, self._raw_fraction())
        return self._last

    def _raw_fraction(self):
        ids = list(self.expected) or list(self.total)
        if not ids:
            return 0.0
        if len(ids) == 1:
            fid = ids[0]
            t = self.total.get(fid) or self.expected.get(fid)
            return min(1.0, (self.done.get(fid, 0) / t)) if t else 0.0
        num = den = 0.0
        unit = {}
        for fid in ids:
            exp = self.expected.get(fid) or 1.0
            unit[fid] = exp
            den += exp
        for fid in ids:
            t = self.total.get(fid)
            d = self.done.get(fid, 0)
            frac = min(1.0, d / t) if t else 0.0
            num += unit[fid] * frac
        return num / den if den else 0.0

    def bytes(self):
        d = sum(self.done.values())
        t = 0
        for fid in (list(self.expected) or list(self.total)):
            t += self.total.get(fid) or (self.expected.get(fid) if (self.expected.get(fid) or 0) > 1000 else 0) or 0
        return d, t


def download(task: dict, settings, out_dir: Path, on_update, cancel_event: threading.Event):
    """阻塞执行下载；on_update(dict) 回报进度；返回最终文件路径。

    优先走多连接加速；遇到分片流（m3u8/DASH）、多视频推文或加速失败，自动回退到 yt-dlp 原生下载。
    """
    if settings.get("accelerate", True) and task.get("count", 1) <= 1:
        try:
            path = fast_download(task, settings, out_dir, on_update, cancel_event)
            if path:
                return path
        except fastdl.Canceled:
            raise EngineError("已取消")
        except Exception as e:  # noqa: 加速失败不影响下载，回退即可
            if cancel_event.is_set():
                raise EngineError("已取消")
            task["_fast_error"] = str(e)
            on_update({"status": "downloading", "progress": 0.0, "speed": 0, "eta": None,
                       "downloaded": 0, "total": 0})
    return native_download(task, settings, out_dir, on_update, cancel_event)


def native_download(task: dict, settings, out_dir: Path, on_update, cancel_event: threading.Event):
    args = base_args(task["platform"], settings)
    args += format_args(task["quality"], settings)
    if settings.get("accelerate", True):
        args += ["-N", str(int(settings.get("fragments", 8) or 8))]
    args += [
        "--newline", "--progress", "--no-simulate", "--no-mtime",
        "--progress-template", _PROGRESS_TMPL,
        "--print", _FORMATS_TMPL,
        "--print", f"after_move:{_D}|%(filepath)s",
        "-P", str(out_dir),
        "-o", settings["filename_template"] or "%(title).60s [%(id)s].%(ext)s",
        "--", task["url"],
    ]
    if task.get("count", 1) > 1:
        args.remove("--no-playlist")
        args.insert(1, "--yes-playlist")

    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)
    tracker = ProgressTracker()
    files, errors, tail = [], [], []
    watcher = threading.Thread(target=_watch_cancel, args=(proc, cancel_event), daemon=True)
    watcher.start()
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            tail = (tail + [line])[-20:]
            if line.startswith(_P + "|"):
                p = line.split("|")
                if len(p) < 8:
                    continue
                _, status, done, total, est, speed, eta, fid = p[:8]
                tracker.update(fid, _num(done), _num(total) or _num(est))
                if status == "finished":
                    tracker.finished.add(str(fid))
                    tracker.done[str(fid)] = tracker.total.get(str(fid)) or tracker.done.get(str(fid), 0)
                    if tracker.all_finished():
                        # 所有流下载完，接下来是合并 / 转音频（--print 会静默 yt-dlp 自己的日志）
                        on_update({"status": "merging", "progress": 1.0, "speed": 0, "eta": None})
                        continue
                d, t = tracker.bytes()
                on_update({
                    "status": "downloading",
                    "progress": tracker.fraction(),
                    "speed": _num(speed) or 0,
                    "eta": _num(eta),
                    "downloaded": d,
                    "total": t,
                })
            elif line.startswith(_F + "|"):
                p = line.split("|")
                ids = _jlist(p[1]) if len(p) > 1 else None
                if ids:
                    s1 = _jlist(p[2]) or []
                    s2 = _jlist(p[3]) or []
                    sizes = [(s1[i] if i < len(s1) and s1[i] else (s2[i] if i < len(s2) else None)) for i in range(len(ids))]
                    tracker.set_formats(ids, sizes)
                elif len(p) > 4 and p[4] not in ("NA", ""):
                    tracker.set_formats([p[4]], [_num(p[5]) if len(p) > 5 else None])
            elif line.startswith(_D + "|"):
                files.append(line[len(_D) + 1:])
            elif _POST_RE.match(line):
                on_update({"status": "merging", "progress": 1.0, "speed": 0, "eta": None})
            elif line.startswith("ERROR:"):
                errors.append(line)
        proc.wait()
    finally:
        if proc.poll() is None:
            kill_tree(proc)
    if task.get("_canceled") or cancel_event.is_set():
        raise EngineError("已取消")
    if proc.returncode != 0:
        raise EngineError(explain_error("\n".join(errors or tail)))
    if not files:
        # 已经下载过时 yt-dlp 不会触发 after_move
        for l in tail:
            m = re.search(r"\[download\] (.+?) has already been downloaded", l)
            if m:
                files.append(m.group(1))
    return files[-1] if files else None


def _watch_cancel(proc, ev: threading.Event):
    while proc.poll() is None:
        if ev.wait(0.3):
            if proc.poll() is None:
                kill_tree(proc)
            return


def explain_error(text: str) -> str:
    """把 yt-dlp 的英文报错翻成人话。"""
    t = (text or "").strip()
    low = t.lower()
    rules = [
        ("sign in to confirm you", "YouTube 要求验证不是机器人：换个代理节点，或在设置里给 YouTube 导入 Cookie"),
        ("no supported javascript runtime", "缺少 Deno：到「设置 → 组件」安装后重试"),
        ("video unavailable", "视频不可用（被删除/私密/地区限制）；如果其他视频也这样，先更新 yt-dlp 并安装 Deno"),
        ("this video is not available", "视频不可用；如果其他视频也这样，先更新 yt-dlp 并安装 Deno"),
        ("private video", "这是私密视频，需要有权限的账号 Cookie"),
        ("members-only", "会员专属内容，需要对应账号的 Cookie"),
        ("confirm your age", "有年龄限制，需要导入已登录账号的 Cookie"),
        ("could not copy chrome cookie database", "读不了 Chrome/Edge 的 Cookie（Windows 加密限制），请改用 cookies.txt 文件或 Firefox"),
        ("failed to decrypt with dpapi", "读不了 Chrome/Edge 的 Cookie（Windows 加密限制），请改用 cookies.txt 文件或 Firefox"),
        ("requested format is not available", "没有这个清晰度，换一个试试"),
        ("ffmpeg is not installed", "缺少 ffmpeg：到「设置 → 组件」安装"),
        ("ffmpeg not found", "缺少 ffmpeg：到「设置 → 组件」安装"),
        ("unable to download webpage", "网络连不上：YouTube/X 需要开代理，检查设置里的代理"),
        ("timed out", "网络超时：检查代理是否可用"),
        ("connection refused", "代理连接被拒：代理软件是否开着、端口是否正确"),
        ("http error 403", "服务器拒绝（403）：先更新 yt-dlp，或导入 Cookie 再试"),
        ("http error 429", "请求太频繁（429）：稍后再试或换个节点"),
        ("no video could be found in this tweet", "这条推文里没有视频"),
        ("nsfw", "敏感内容需要登录：给 X 导入 Cookie"),
        ("unsupported url", "不支持这个链接"),
        ("fresh cookies", "需要新鲜的 Cookie：在浏览器打开一次该页面后重新导出"),
    ]
    for key, msg in rules:
        if key and key in low:
            return msg
    errs = [l for l in t.splitlines() if l.startswith("ERROR:")]
    line = (errs[-1] if errs else (t.splitlines()[-1] if t else "未知错误"))
    return line.replace("ERROR: ", "")[:300]


# ---------------- 多连接加速 ----------------

def _plan(task, settings, out_dir):
    """让 yt-dlp 只做“选格式 + 算文件名”，返回 info。"""
    args = base_args(task["platform"], settings)
    args += format_args(task["quality"], settings)
    args += ["--print", "SYN|%(filename)s", "--print", "%()j", "-P", str(out_dir),
             "-o", settings["filename_template"] or "%(title).60s [%(id)s].%(ext)s", "--", task["url"]]
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=90, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise EngineError("解析超时：检查网络或代理设置")
    if r.returncode != 0:
        raise EngineError(explain_error(r.stderr or r.stdout))
    info, name = None, None
    for line in r.stdout.splitlines():
        if line.startswith("SYN|"):
            name = line[4:].strip()
        elif line.startswith("{"):
            info = json.loads(line)
    if not info:
        raise EngineError("解析结果为空")
    info["filename"] = name
    return info


def _ffmpeg(args, cancel_event):
    ff = components.find("ffmpeg")
    if not ff:
        raise EngineError("缺少 ffmpeg：到「设置 → 组件」安装")
    proc = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-y", *args],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    while proc.poll() is None:
        if cancel_event.wait(0.2):
            kill_tree(proc)
            raise fastdl.Canceled("已取消")
    if proc.returncode != 0:
        raise EngineError("ffmpeg 处理失败：" + (proc.stderr.read() or "")[-200:])


def fast_download(task, settings, out_dir: Path, on_update, cancel_event):
    """返回最终路径；返回 None 表示这个视频不适合加速（交给 yt-dlp）。"""
    out_dir = Path(out_dir)
    info = _plan(task, settings, out_dir)
    if info.get("_type") == "playlist":
        return None
    fmts = info.get("requested_formats") or [info]
    if not all(f.get("url") and (f.get("protocol") or "https") in ("http", "https") for f in fmts):
        return None                       # m3u8 / DASH 分片流：yt-dlp 的并发分片更合适
    name = info.get("filename") or info.get("_filename")
    if not name:
        return None
    final = Path(name)
    if not final.is_absolute():
        final = out_dir / final
    quality = task["quality"]
    if quality == "audio_m4a":
        final = final.with_suffix(".m4a")
    elif quality == "audio_mp3":
        final = final.with_suffix(".mp3")
    if final.exists() and final.stat().st_size > 0:
        return str(final)

    proxy = resolve_proxy(settings, task["platform"])
    conns = int(settings.get("connections", 16) or 16)

    # 先探测每条流的大小，进度按总字节算
    known = []
    for f in fmts:
        if cancel_event.is_set():
            raise fastdl.Canceled("已取消")
        known.append(fastdl.probe(f["url"], f.get("http_headers"), proxy))
    total = sum(k[0] for k in known)
    meter = fastdl.Meter()
    last = [0.0]

    def tick(force=False):
        now = time.time()
        if not force and now - last[0] < 0.25:
            return
        last[0] = now
        sp = meter.speed()
        done = meter.done
        on_update({"status": "downloading", "progress": (done / total) if total else 0.0,
                   "speed": sp, "eta": ((total - done) / sp) if (sp > 0 and total) else None,
                   "downloaded": done, "total": total})

    tick(force=True)
    temps = []
    try:
        for f, k in zip(fmts, known):
            tmp = final.with_name(f"{final.stem}.f{f.get('format_id', 'x')}.{f.get('ext') or 'bin'}")
            temps.append(tmp)
            fastdl.download(f["url"], tmp, headers=f.get("http_headers"), proxy=proxy, connections=conns,
                            cancel=cancel_event, on_progress=tick, meter=meter, known=k)
        tick(force=True)
        if cancel_event.is_set():
            raise fastdl.Canceled("已取消")

        on_update({"status": "merging", "progress": 1.0, "speed": 0, "eta": None})
        if quality == "audio_m4a":
            ac = (fmts[0].get("acodec") or "")
            codec = ["-c:a", "copy"] if ac.startswith("mp4a") else ["-c:a", "aac", "-b:a", "192k"]
            _ffmpeg(["-i", str(temps[0]), "-vn", *codec, "-movflags", "+faststart", str(final)], cancel_event)
        elif quality == "audio_mp3":
            _ffmpeg(["-i", str(temps[0]), "-vn", "-c:a", "libmp3lame", "-q:a", "0", str(final)], cancel_event)
        elif len(temps) == 2:
            extra = ["-movflags", "+faststart"] if final.suffix.lower() in (".mp4", ".m4a", ".mov") else []
            _ffmpeg(["-i", str(temps[0]), "-i", str(temps[1]), "-map", "0:v:0", "-map", "1:a:0",
                     "-c", "copy", *extra, str(final)], cancel_event)
        else:
            temps[0].replace(final)
        return str(final)
    finally:
        for t in temps:
            for p in (t, t.with_name(t.name + ".part")):
                try:
                    if p.exists() and p != final:
                        p.unlink()
                except OSError:
                    pass
