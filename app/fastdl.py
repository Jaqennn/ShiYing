"""多连接分块下载（类似 IDM）：把文件切成小块，多个连接同时下，按偏移写入同一个文件。

- 先用 Range: bytes=0-0 探测总大小和是否支持分块；不支持就退回单连接
- 工作线程从队列里领取分块，失败的块会重试，不会整个文件重来
- 连接是一个一个加上去的；服务器（比如网盘）拒绝多出来的连接时，被拒的连接自己退出，
  其余连接照常下载，隔一会儿再试着加回来——不会因为几个连接被拒就整个文件重来
- 进度精确到字节，可随时取消；meter.conns 是正在工作的连接数
"""
import queue
import re
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
        self.conns = 0          # 正在工作的连接数（界面显示「16 线程」用）

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
    meter.conns = 1
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


# 这些错误说明地址本身不能用，换多少连接都没用，直接失败
_HARD_RE = re.compile(r"HTTP (400|401|404|410|412|416)\b")
_MAX_TRIES = 10          # 同一块失败这么多次才放弃整个文件
_RAMP = 0.1              # 新连接之间的间隔（秒）


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0          # 活着的连接（线程）数
        self.inflight = 0        # 正在下载中的分块数
        self.errors = []
        self.fatal = threading.Event()
        self.last_retire = 0.0   # 最近一次有连接被拒、退出的时间
        self.last_grow = 0.0     # 最近一次试着加回连接的时间
        self.grow_wait = 15.0    # 隔多久试着加回一个连接；加回来又被拒就加倍，最多 2 分钟


def _multi(url, part, total, headers, proxy, n, cancel, meter, on_progress):
    with open(part, "wb") as f:
        f.truncate(total)
    psize = _piece_size(total, n)
    pieces = queue.Queue()
    for start in range(0, total, psize):
        pieces.put((start, min(start + psize, total) - 1, 0))
    st = _State()
    threads = []

    def worker(grown=False):
        s = _session(headers, proxy)
        fh = open(part, "r+b")
        counted = True
        fails = 0
        try:
            while not cancel.is_set() and not st.fatal.is_set():
                try:
                    start, end, tries = pieces.get(timeout=0.3)
                except queue.Empty:
                    with st.lock:
                        if st.inflight == 0 and pieces.empty():
                            return  # 全部下完了
                    continue        # 别的连接失败时会把分块放回来，等一下
                with st.lock:
                    st.inflight += 1
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
                            if pos > end:
                                break
                    if pos <= end:
                        raise FastDLError("连接中断")
                    fails = 0
                    if grown:  # 加回来的连接能用：下次早点再加一个
                        grown = False
                        with st.lock:
                            st.grow_wait = max(3.0, st.grow_wait / 2)
                except Exception as e:  # 这一块没下完：剩余部分放回队列，别的连接会接着下
                    if pos <= end:
                        pieces.put((pos, end, tries + 1))
                    msg = str(e) or e.__class__.__name__
                    retire = False
                    with st.lock:
                        st.errors = (st.errors + [msg])[-20:]
                        if _HARD_RE.search(msg) or tries + 1 >= _MAX_TRIES:
                            st.fatal.set()
                        else:
                            fails += 1
                            # 连着被拒两次、而且还有别的连接在干活：说明服务器不想要这么多连接，这个连接退出
                            if fails >= 2 and st.active > 1:
                                now = time.time()
                                if now - st.last_grow < st.grow_wait:
                                    st.grow_wait = min(st.grow_wait * 2, 120.0)  # 刚加回来又被拒：下次晚点再试
                                st.active -= 1
                                meter.conns = st.active
                                counted = False
                                st.last_retire = now
                                retire = True
                    if retire or st.fatal.is_set():
                        return
                    time.sleep(min(5.0, 0.5 * (tries + 1)))
                finally:
                    with st.lock:
                        st.inflight -= 1
        finally:
            fh.close()
            with st.lock:
                if counted:
                    st.active -= 1
                    meter.conns = st.active

    def spawn(grown=False):
        with st.lock:
            st.active += 1
            meter.conns = st.active
        t = threading.Thread(target=worker, args=(grown,), daemon=True)
        threads.append(t)
        t.start()

    # 先开 4 个，其余一个一个加：同一瞬间开一堆新连接，有的网盘会直接拒绝
    want = min(n, pieces.qsize())
    for i in range(want):
        if cancel.is_set() or st.fatal.is_set():
            break
        spawn()
        if 3 <= i < want - 1:
            time.sleep(_RAMP)

    while not cancel.is_set() and not st.fatal.is_set():
        time.sleep(0.3)
        with st.lock:
            alive, inflight = st.active, st.inflight
        if alive == 0:
            if pieces.empty() and inflight == 0:
                break   # 全部下完
            spawn()     # 没有连接在干活但还有分块：补一个
            continue
        now = time.time()
        if (alive < n and not pieces.empty() and st.last_retire
                and now - st.last_retire > st.grow_wait and now - st.last_grow > st.grow_wait):
            st.last_grow = now
            spawn(grown=True)  # 隔一会儿试着加回一个连接：网盘放宽了就能用上

    for t in threads:
        t.join(timeout=60)
    meter.conns = 0
    if st.fatal.is_set() and not cancel.is_set():
        part.unlink(missing_ok=True)
        raise FastDLError("分块下载失败：" + (st.errors[-1] if st.errors else "未知"))
    if not cancel.is_set() and not pieces.empty():
        raise FastDLError("还有分块没下完")
