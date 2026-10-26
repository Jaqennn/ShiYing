"""多连接分块下载（类似 IDM）：把文件切成小块，多个连接同时下，按偏移写入同一个文件。

- 先用 Range: bytes=0-0 探测总大小和是否支持分块；不支持就退回单连接
- 工作线程从队列里领取分块，失败的块会重试，不会整个文件重来
- 进度精确到字节，可随时取消
"""
import queue
import threading
import time
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


class FastDLError(Exception):
    pass


class Canceled(FastDLError):
    pass


def _session(headers, proxy):
    s = requests.Session()
    s.trust_env = False
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"})
    s.headers.update(headers or {})
    return s


def probe(url, headers=None, proxy=None, timeout=20):
    """返回 (总大小, 是否支持分块, Content-Type, 跳转后的最终地址)。"""
    s = _session(headers, proxy)
    with s.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=timeout, allow_redirects=True) as r:
        if r.status_code >= 400:
            raise FastDLError(f"HTTP {r.status_code}")
        ctype = r.headers.get("Content-Type", "")
        cr = r.headers.get("Content-Range", "")
        if r.status_code == 206 and "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            return int(cr.rsplit("/", 1)[1]), True, ctype, r.url
        return int(r.headers.get("Content-Length") or 0), False, ctype, r.url


class Meter:
    """全局字节计数 + 最近 3 秒的平均速度。"""

    def __init__(self, base=0):
        self.done = base
        self.lock = threading.Lock()
        self.samples = [(time.time(), base)]

    def add(self, n):
        with self.lock:
            self.done += n

    def speed(self):
        now = time.time()
        with self.lock:
            d = self.done
        self.samples.append((now, d))
        self.samples = [x for x in self.samples if now - x[0] <= 3.0] or [(now, d)]
        t0, d0 = self.samples[0]
        return (d - d0) / (now - t0) if now > t0 else 0.0


def download(url, dest: Path, *, headers=None, proxy=None, connections=16, cancel: threading.Event,
             on_progress=None, meter: Meter = None, known=None):
    """下载到 dest。on_progress() 无参数，由调用方从 meter 读进度。known=(total, ranges, final_url)。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total, ranges, _, final = known or probe(url, headers, proxy)
    url = final or url
    part = dest.with_name(dest.name + ".part")
    meter = meter or Meter()
    connections = max(1, int(connections))

    if not ranges or total < 2 * 1024 * 1024 or connections == 1:
        _single(url, part, headers, proxy, cancel, meter, on_progress)
    else:
        _multi(url, part, total, headers, proxy, connections, cancel, meter, on_progress)
    if cancel.is_set():
        part.unlink(missing_ok=True)
        raise Canceled("已取消")
    if total and part.stat().st_size != total:
        part.unlink(missing_ok=True)
        raise FastDLError("文件大小不对，下载不完整")
    part.replace(dest)
    return dest


def _single(url, part, headers, proxy, cancel, meter, on_progress):
    s = _session(headers, proxy)
    with s.get(url, stream=True, timeout=30) as r:
        if r.status_code >= 400:
            raise FastDLError(f"HTTP {r.status_code}")
        with open(part, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if cancel.is_set():
                    return
                f.write(chunk)
                meter.add(len(chunk))
                if on_progress:
                    on_progress()


def _piece_size(total, n):
    # 每个连接分到约 4 块，单块 1–8MB（太大的 Range 容易被 YouTube 限速）
    size = total // (n * 4)
    return max(1 << 20, min(8 << 20, size))


def _multi(url, part, total, headers, proxy, n, cancel, meter, on_progress):
    with open(part, "wb") as f:
        f.truncate(total)
    psize = _piece_size(total, n)
    pieces = queue.Queue()
    for start in range(0, total, psize):
        pieces.put((start, min(start + psize, total) - 1, 0))
    errors = []
    err_lock = threading.Lock()
    failed = threading.Event()

    def worker():
        s = _session(headers, proxy)
        fh = open(part, "r+b")
        try:
            while not cancel.is_set() and not failed.is_set():
                try:
                    start, end, tries = pieces.get_nowait()
                except queue.Empty:
                    return
                pos = start
                try:
                    with s.get(url, headers={"Range": f"bytes={start}-{end}"}, stream=True, timeout=(15, 30)) as r:
                        if r.status_code != 206:
                            raise FastDLError(f"HTTP {r.status_code}")
                        fh.seek(start)
                        for chunk in r.iter_content(128 * 1024):
                            if cancel.is_set():
                                return
                            chunk = chunk[: end + 1 - pos]
                            fh.write(chunk)
                            pos += len(chunk)
                            meter.add(len(chunk))
                            if on_progress:
                                on_progress()
                    if pos <= end:
                        raise FastDLError("连接中断")
                except Exception as e:  # 这一块没下完：剩余部分放回队列重试
                    if tries >= 4:
                        with err_lock:
                            errors.append(str(e))
                        failed.set()
                        return
                    pieces.put((pos, end, tries + 1))
                    time.sleep(0.5 * (tries + 1))
        finally:
            fh.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(n, pieces.qsize()))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if failed.is_set():
        part.unlink(missing_ok=True)
        raise FastDLError("分块下载失败：" + (errors[-1] if errors else "未知"))
    if not cancel.is_set() and not pieces.empty():
        raise FastDLError("还有分块没下完")
