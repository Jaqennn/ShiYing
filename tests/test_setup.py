"""安装向导、组件下载、反馈信息的测试（不联网，用假的组件管理器和本地服务器）。"""
import hashlib
import http.server
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import components, feedback, fetch, setup  # noqa: E402


class FakeSettings(dict):
    def update(self, patch):
        super().update(patch)

    def __getitem__(self, k):
        return self.get(k, "")


class FakeComps:
    """假的 ComponentManager：install() 按设定的速度假装下载，可以失败或被停止。"""

    def __init__(self, installed=(), fail=(), seconds=0.15):
        self.have = set(installed)
        self.fail = set(fail)
        self.seconds = seconds
        self.state = {n: {"status": "idle", "done": 0, "total": 0, "speed": 0, "progress": 0.0, "message": ""}
                      for n in components.SOURCES}
        self.stops = {n: threading.Event() for n in components.SOURCES}
        self.calls = []

    def status(self):
        return [{"name": n, "path": f"C:/bin/{n}.exe" if n in self.have else None, "version": "1.0" if n in self.have else None,
                 "approx": components.SOURCES[n]["approx"], **self.state[n]} for n in components.SOURCES]

    def get(self, n):
        return dict(self.state[n])

    def installed(self, n):
        return n in self.have

    def stop(self, n):
        self.stops[n].set()

    def install(self, n):
        self.calls.append(n)
        self.stops[n].clear()
        total = 1_000_000
        st = self.state[n]
        st.update(status="working", done=0, total=total)
        steps = 10
        for i in range(1, steps + 1):
            if self.stops[n].is_set():
                st.update(status="idle", message="已停止")
                return False
            time.sleep(self.seconds / steps)
            st.update(done=total * i // steps, speed=total / self.seconds, progress=i / steps)
        if n in self.fail:
            st.update(status="error", message="连不上 GitHub")
            return False
        self.have.add(n)
        st.update(status="done", message="已安装 1.0")
        return True


class FakeAssets:
    def __init__(self, nvidia=True, engine=False):
        self.nvidia = nvidia
        self.engine = engine
        self.state = {}
        self.calls = []

    def status(self):
        return {"engine": {"installed": self.engine, "gpu": self.nvidia, "build": ""}, "nvidia": self.nvidia,
                "models": [{"key": "base.en", "label": "快速", "desc": "", "size": 148_000_000, "installed": False},
                           {"key": "small.en", "label": "均衡", "desc": "", "size": 488_000_000, "installed": False},
                           {"key": "large-v3-turbo", "label": "精准", "desc": "", "size": 574_000_000, "installed": False}],
                "vad": {"installed": False}}

    def get(self, key):
        return dict(self.state.get(key) or {"status": "idle", "done": 0, "total": 0, "speed": 0})

    def stop(self, key):
        pass

    def install_engine(self, gpu):
        self.calls.append(("engine", gpu))
        self.state["engine"] = {"status": "done", "done": 5, "total": 5, "message": "GPU 版已安装"}
        return True

    def install_model(self, key):
        self.calls.append(("model", key))
        self.state["model:" + key] = {"status": "done", "done": 9, "total": 9, "message": "已下载"}
        return True


def wait(mgr, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if mgr.status()["status"] != "working":
            return mgr.status()
        time.sleep(0.02)
    raise AssertionError("安装没在规定时间里结束")


def make(**kw):
    comps = FakeComps(**{k: v for k, v in kw.items() if k in ("installed", "fail", "seconds")})
    assets = FakeAssets(**{k: v for k, v in kw.items() if k in ("nvidia", "engine")})
    return setup.SetupManager(FakeSettings(github_mirror=""), comps, assets), comps, assets


# ---------- 清单 ----------
def test_plan_lists_missing_and_recommends_model():
    mgr, _, _ = make(installed={"yt-dlp"}, nvidia=True)
    p = mgr.plan()
    assert p["missing"] == ["ffmpeg", "deno"]
    keys = [it["key"] for it in p["items"]]
    assert keys[:4] == ["yt-dlp", "ffmpeg", "deno", "engine"]
    assert p["recommended_model"] == "model:large-v3-turbo"
    eng = next(it for it in p["items"] if it["key"] == "engine")
    assert eng["size"] == setup.ENGINE_SIZE[True] and "GPU" in eng["desc"]
    mgr2, _, _ = make(nvidia=False)
    p2 = mgr2.plan()
    assert p2["recommended_model"] == "model:small.en"
    assert next(it for it in p2["items"] if it["key"] == "engine")["size"] == setup.ENGINE_SIZE[False]


# ---------- 一键安装 ----------
def test_install_in_order_and_report_progress():
    mgr, comps, assets = make(nvidia=True)
    v = mgr.start(["model:large-v3-turbo", "deno", "engine", "yt-dlp", "ffmpeg", "bogus"])
    assert [it["key"] for it in v["items"]] == ["yt-dlp", "ffmpeg", "deno", "engine", "model:large-v3-turbo"]
    seen_progress = False
    end = time.time() + 5
    while mgr.status()["status"] == "working" and time.time() < end:
        s = mgr.status()
        if s["current"] and 0 < s["done"] < s["total"]:
            seen_progress = True
            assert s["index"] >= 1 and s["count"] == 5
        time.sleep(0.01)
    s = wait(mgr)
    assert s["status"] == "done", s
    assert seen_progress
    assert comps.calls == ["yt-dlp", "ffmpeg", "deno"]
    assert assets.calls == [("engine", True), ("model", "large-v3-turbo")]
    assert all(it["status"] == "done" for it in s["items"])
    assert s["done"] == s["total"]


def test_failure_keeps_going_then_retry():
    mgr, comps, _ = make(fail={"ffmpeg"})
    mgr.start(["yt-dlp", "ffmpeg", "deno"])
    s = wait(mgr)
    assert s["status"] == "error"
    st = {it["key"]: it["status"] for it in s["items"]}
    assert st == {"yt-dlp": "done", "ffmpeg": "error", "deno": "done"}
    assert "GitHub" in next(it for it in s["items"] if it["key"] == "ffmpeg")["message"]
    comps.fail.clear()
    mgr.retry()
    s = wait(mgr)
    assert s["status"] == "done"
    assert comps.calls.count("ffmpeg") == 2 and comps.calls.count("yt-dlp") == 1


def test_stop_and_mirror_restart():
    mgr, comps, _ = make(seconds=1.0)
    mgr.start(["yt-dlp", "ffmpeg"])
    time.sleep(0.25)
    mgr.use_mirror()                       # 正在下 yt-dlp：停下来，用镜像重来
    assert mgr.settings["github_mirror"] == setup.DEFAULT_MIRROR
    time.sleep(0.2)
    assert comps.calls.count("yt-dlp") == 2
    mgr.stop()
    s = wait(mgr)
    assert s["status"] == "stopped"
    assert {it["key"]: it["status"] for it in s["items"]} == {"yt-dlp": "stopped", "ffmpeg": "waiting"}
    with pytest.raises(ValueError):
        mgr.use_mirror("ftp://x")


def test_slow_download_suggests_mirror(monkeypatch):
    monkeypatch.setattr(setup, "SLOW_AFTER", 0.05)
    monkeypatch.setattr(setup, "SLOW_BPS", 10**12)   # 任何速度都算慢
    mgr, _, _ = make(seconds=0.6)
    mgr.start(["ffmpeg"])
    time.sleep(0.3)
    assert mgr.status()["slow"] is True
    mgr.settings["github_mirror"] = "https://mirror.example/"
    assert mgr.status()["slow"] is False     # 已经在用镜像了就不再提示
    wait(mgr)


def test_start_requires_something():
    mgr, _, _ = make()
    with pytest.raises(ValueError):
        mgr.start(["nothing"])


# ---------- 下载：多连接、停止、校验 ----------
class _Files(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture()
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    data = bytes(range(256)) * 12_000   # 3MB 左右，会走多连接
    (root / "f.bin").write_bytes(data)

    def handler(*a, **kw):
        return _Files(*a, directory=str(root), **kw)

    class Quiet(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):   # 停止下载时客户端会断开，不用打印
            pass

    httpd = Quiet(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", data
    httpd.shutdown()


def test_fetch_download_checks_digest(server, tmp_path):
    base, data = server
    good = hashlib.sha256(data).hexdigest()
    seen = []
    out = fetch.download(base + "/f.bin", tmp_path / "a.bin", stop=threading.Event(),
                         on_bytes=lambda d, t, s: seen.append((d, t)), digest=good)
    assert out.read_bytes() == data and seen[-1] == (len(data), len(data))
    with pytest.raises(fetch.FetchError, match="校验"):
        fetch.download(base + "/f.bin", tmp_path / "b.bin", stop=threading.Event(), digest="0" * 64)
    assert not (tmp_path / "b.bin").exists()
    with pytest.raises(fetch.FetchError, match="404"):
        fetch.download(base + "/missing.bin", tmp_path / "c.bin", stop=threading.Event())


def test_fetch_stop(server, tmp_path):
    base, _ = server
    stop = threading.Event()
    stop.set()
    with pytest.raises(fetch.Stopped):
        fetch.download(base + "/f.bin", tmp_path / "d.bin", stop=stop)


def test_mirror_and_digest_helpers():
    assert fetch.with_mirror("https://github.com/a/b", "https://ghfast.top") == "https://ghfast.top/https://github.com/a/b"
    assert fetch.with_mirror("https://hf-mirror.com/x", "https://ghfast.top/") == "https://hf-mirror.com/x"
    assert fetch.with_mirror("https://github.com/a/b", "") == "https://github.com/a/b"
    assets = [{"name": "yt-dlp.exe", "digest": "sha256:" + "ab" * 32}, {"name": "x.zip", "digest": None}]
    assert fetch.digest_of(assets, "yt-dlp.exe") == "ab" * 32
    assert fetch.digest_of(assets, "x.zip") is None and fetch.digest_of(assets, "nope") is None


# ---------- 反馈信息 ----------
def test_feedback_collect():
    comps = [{"name": "yt-dlp", "version": "2026.10.22"}, {"name": "ffmpeg", "version": None}, {"name": "deno", "version": "2.5.4"}]
    assets = {"engine": {"installed": True, "gpu": True, "build": "whisper-cuda b5454"}, "nvidia": True,
              "models": [{"label": "精准", "installed": True}, {"label": "快速", "installed": False}]}
    info = feedback.collect(comps, assets, {"site": "哔哩哔哩", "error": "HTTP 412", "url": "https://b23.tv/x", "title": ""})
    text = info["text"]
    assert "拾影：" in text and "yt-dlp：2026.10.22" in text and "ffmpeg：未安装" in text
    assert "字幕引擎：GPU 版（whisper-cuda b5454）" in text and "识别模型：精准" in text
    assert "出错的网站：哔哩哔哩" in text and "错误提示：HTTP 412" in text and "视频标题" not in text
    assert info["issue_url"].startswith(feedback.ISSUE_URL + "&system=")
    assert feedback.allowed_url(feedback.ISSUE_URL) and feedback.allowed_url("https://wj.qq.com/s2/1/x/")
    assert not feedback.allowed_url("https://evil.example/") and not feedback.allowed_url(None)
