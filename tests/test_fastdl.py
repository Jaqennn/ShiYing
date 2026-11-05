"""多线程下载引擎测试：本机模拟服务器，不联网。"""
import hashlib
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import fastdl  # noqa: E402


class _FileServer:
    """模拟下载服务器：支持 Range，记录请求头和同时连接数。
    limit：超过这么多同时连接就返回 503；need_ua：UA 不对返回 403；piece_404：分块请求一律 404；delay：每 64KB 停一下。"""

    def __init__(self, data, limit=0, need_ua="", piece_404=False, delay=0.002):
        self.data, self.limit, self.need_ua, self.piece_404, self.delay = data, limit, need_ua, piece_404, delay
        self.active = self.peak = self.rejected = 0
        self.seen = []
        self.lock = threading.Lock()
        fs = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                with fs.lock:
                    fs.active += 1
                    fs.peak = max(fs.peak, fs.active)
                    busy = fs.limit and fs.active > fs.limit
                    fs.seen.append(dict(self.headers))
                try:
                    rng = self.headers.get("Range")
                    if fs.need_ua and self.headers.get("User-Agent") != fs.need_ua:
                        self.send_response(403); self.end_headers(); return
                    if fs.piece_404 and rng and rng != "bytes=0-0":
                        self.send_response(404); self.end_headers(); return
                    if busy:
                        with fs.lock:
                            fs.rejected += 1
                        self.send_response(503); self.end_headers(); return
                    body, code = fs.data, 200
                    if rng:
                        a, b = rng.split("=")[1].split("-")
                        a, b = int(a), (int(b) if b else len(fs.data) - 1)
                        body, code = fs.data[a:b + 1], 206
                    self.send_response(code)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Accept-Ranges", "bytes")
                    if rng:
                        self.send_header("Content-Range", f"bytes {a}-{a + len(body) - 1}/{len(fs.data)}")
                    self.end_headers()
                    for i in range(0, len(body), 64 * 1024):
                        self.wfile.write(body[i:i + 64 * 1024])
                        time.sleep(fs.delay)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with fs.lock:
                        fs.active -= 1

        class S(ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 64   # Windows 上队列满了会直接拒绝连接

        self.httpd = S(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/file.bin?sign=x"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


DATA = os.urandom(6 * 1024 * 1024 + 12345)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def test_multi_connection_with_headers(tmp_path):
    fs = _FileServer(DATA, need_ua="ShiYing-test")
    try:
        meter = fastdl.Meter()
        conns = []
        dest = fastdl.download(fs.url, tmp_path / "a.bin", headers={"User-Agent": "ShiYing-test", "Cookie": "k=v"},
                               connections=8, cancel=threading.Event(), meter=meter,
                               on_progress=lambda: conns.append(meter.conns))
        assert _sha(Path(dest).read_bytes()) == _sha(DATA)
        assert fs.peak >= 4, fs.peak                       # 真的是多连接
        assert all(h.get("Cookie") == "k=v" for h in fs.seen)
        assert max(conns) >= 4 and meter.conns == 0        # 下载中报告了线程数，下完归零
        assert not list(tmp_path.glob("*.part"))
    finally:
        fs.close()


def test_keeps_the_connections_the_server_allows(tmp_path):
    """服务器只允许 3 个连接：多出来的连接自己退出，剩下的接着下，一次下完，不会整个文件报错。"""
    fs = _FileServer(DATA, limit=3)
    try:
        dest = fastdl.download(fs.url, tmp_path / "b.bin", connections=16, cancel=threading.Event())
        assert Path(dest).read_bytes() == DATA
        assert fs.rejected > 0 and fs.peak <= 5
    finally:
        fs.close()


def test_server_that_allows_only_one_connection(tmp_path):
    fs = _FileServer(DATA, limit=1)
    try:
        dest = fastdl.download(fs.url, tmp_path / "c.bin", connections=16, cancel=threading.Event())
        assert Path(dest).read_bytes() == DATA
    finally:
        fs.close()


def test_hard_error_fails_fast(tmp_path):
    fs = _FileServer(DATA, piece_404=True)
    try:
        t0 = time.time()
        with pytest.raises(fastdl.FastDLError, match="404"):
            fastdl.download(fs.url, tmp_path / "d.bin", connections=8, cancel=threading.Event())
        assert time.time() - t0 < 10
        assert not list(tmp_path.glob("*"))
    finally:
        fs.close()


def test_cancel_cleans_up(tmp_path):
    fs = _FileServer(DATA, delay=0.05)   # 慢一点，保证取消时还没下完
    try:
        ev = threading.Event()
        threading.Timer(0.5, ev.set).start()
        with pytest.raises(fastdl.Canceled):
            fastdl.download(fs.url, tmp_path / "e.bin", connections=8, cancel=ev)
        assert not list(tmp_path.glob("*"))
    finally:
        fs.close()
