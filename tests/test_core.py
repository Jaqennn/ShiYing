"""不联网的核心逻辑测试：python -m pytest tests -q"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import detect, douyin, ytdlp  # noqa: E402


# ---------- 链接识别 ----------
CASES = [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s", "youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s"),
    ("看看这个 youtu.be/abcDEF12345，挺好", "youtube", "https://youtu.be/abcDEF12345"),
    ("https://youtube.com/shorts/AbCdEfGhIjk?si=xyz", "youtube", None),
    ("【小猫钓鱼合集】 https://b23.tv/AbC123d", "bilibili", "https://b23.tv/AbC123d"),
    ("https://www.bilibili.com/video/BV1GJ411x7h7/?spm_id_from=333", "bilibili", None),
    ("BV1GJ411x7h7", "bilibili", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("https://twitter.com/someone/status/1790000000000000000", "x", "https://x.com/someone/status/1790000000000000000"),
    ("https://fxtwitter.com/a/status/123", "x", "https://x.com/a/status/123"),
    ("7.43 复制打开抖音，看看【某某的作品】今天天气真好 https://v.douyin.com/iAbCdEf/ yGm:/ 08/12 Q@k.Ok", "douyin", "https://v.douyin.com/iAbCdEf/"),
    ("https://v.douyin.com/iAbCdEf/复制此链接", "douyin", "https://v.douyin.com/iAbCdEf/"),
    ("https://www.douyin.com/jingxuan?modal_id=7400000000000000001", "douyin", None),
    ("https://vimeo.com/123456", "other", None),
]


def test_detect():
    for text, plat, url in CASES:
        r = detect.extract(text)
        assert r, text
        assert r["platform"] == plat, (text, r)
        if url:
            assert r["url"] == url, (text, r)
    assert detect.extract("没有链接的文本") is None


def test_extract_all():
    text = """https://www.youtube.com/watch?v=aaa
【小猫】 https://b23.tv/AbC123d
7.43 复制打开抖音，看看【作品】 https://v.douyin.com/iAbCdEf/ yGm:/
https://x.com/u/status/1https://x.com/u/status/2
https://www.youtube.com/watch?v=aaa
youtu.be/bbb BV1GJ411x7h7"""
    got = [(x["platform"], x["url"]) for x in detect.extract_all(text)]
    assert got == [
        ("youtube", "https://www.youtube.com/watch?v=aaa"),
        ("bilibili", "https://b23.tv/AbC123d"),
        ("douyin", "https://v.douyin.com/iAbCdEf/"),
        ("x", "https://x.com/u/status/1"),
        ("x", "https://x.com/u/status/2"),
        ("youtube", "https://youtu.be/bbb"),
        ("bilibili", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ], got
    # 链接里本身带 BV 号时不重复
    assert len(detect.extract_all("https://www.bilibili.com/video/BV1GJ411x7h7/")) == 1
    assert detect.extract_all("没有链接") == []


def test_douyin_id():
    assert douyin.find_id("https://www.douyin.com/video/7400000000000000001") == "7400000000000000001"
    assert douyin.find_id("https://www.iesdouyin.com/share/video/7400000000000000002/?region=CN") == "7400000000000000002"
    assert douyin.find_id("https://www.douyin.com/jingxuan?modal_id=7400000000000000003") == "7400000000000000003"
    assert douyin.find_id("https://www.douyin.com/note/7400000000000000004") == "7400000000000000004"
    assert douyin.find_id("https://v.douyin.com/iAbCdEf/") is None


# ---------- 抖音分享页解析（用假页面，不联网） ----------
def _fake_page(item):
    data = {"loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": [item]}}}}
    return f"<html><script>window._ROUTER_DATA = {json.dumps(data, ensure_ascii=False)}</script></html>"


class FakeResp:
    def __init__(self, text, url=""):
        self.text, self.url, self.status_code = text, url, 200

    def raise_for_status(self):
        pass

    def close(self):
        pass


class FakeSession:
    def __init__(self, page):
        self.page = page
        self.headers, self.proxies, self.trust_env = {}, {}, False

    def get(self, url, **kw):
        if "v.douyin.com" in url:
            return FakeResp("", "https://www.iesdouyin.com/share/video/7400000000000000009/?from=x")
        return FakeResp(self.page, url)


def test_douyin_parse_video(monkeypatch):
    item = {
        "desc": "海边日落 #旅行 #vlog",
        "author": {"nickname": "小明"},
        "video": {
            "play_addr": {"uri": "v0200fg10000abc", "url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0200fg10000abc&ratio=720p&line=0"]},
            "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpeg"]},
            "duration": 15360, "width": 1080, "height": 1920,
        },
    }
    monkeypatch.setattr(douyin, "_session", lambda proxy=None: FakeSession(_fake_page(item)))
    r = douyin.parse("https://v.douyin.com/iAbCdEf/")
    assert r["id"] == "7400000000000000009"
    assert r["title"].startswith("海边日落")
    assert r["uploader"] == "小明"
    assert r["duration"] == 15
    assert [q["key"] for q in r["qualities"]] == ["1080p", "720p", "540p", "audio_m4a"]
    c = r["_candidates"]["1080p"]
    assert "ratio=1080p" in c[0] and all("playwm" not in u for u in c)


def test_douyin_parse_images(monkeypatch):
    item = {"desc": "图集", "author": {"nickname": "a"}, "video": {},
            "images": [{"url_list": ["https://x/1.webp", "https://x/1.jpeg"]}, {"url_list": ["https://x/2.jpeg"]}]}
    monkeypatch.setattr(douyin, "_session", lambda proxy=None: FakeSession(_fake_page(item)))
    r = douyin.parse("https://www.douyin.com/note/7400000000000000004")
    assert r["kind"] == "images" and r["count"] == 2
    assert r["_candidates"]["images"] == ["https://x/1.jpeg", "https://x/2.jpeg"]


def test_douyin_filter_reason(monkeypatch):
    data = {"loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": [], "filter_list": [{"detail_msg": "作品已删除"}]}}}}
    page = f"<script>window._ROUTER_DATA = {json.dumps(data, ensure_ascii=False)}</script>"
    monkeypatch.setattr(douyin, "_session", lambda proxy=None: FakeSession(page))
    try:
        douyin.parse("https://www.douyin.com/video/7400000000000000001")
        assert False
    except douyin.DouyinError as e:
        assert "作品已删除" in str(e)


def test_safe_name():
    assert douyin._safe_name('a/b:c*?"<>|d #话题 #标签') == "a b c d"


# ---------- yt-dlp 格式与进度 ----------
SETTINGS = {"compat_mode": True, "container": "mp4"}


def test_format_args():
    a = ytdlp.format_args("1080", SETTINGS)
    assert a[a.index("-S") + 1] == "res:1080,fps,vcodec:h264,acodec:m4a"
    assert a[a.index("--merge-output-format") + 1] == "mp4"
    a = ytdlp.format_args("best", {"compat_mode": False, "container": "mkv"})
    assert a[a.index("-S") + 1] == "res" and "mkv" in a
    assert "-x" in ytdlp.format_args("audio_mp3", SETTINGS)


def test_bucket():
    assert ytdlp._bucket(1080) == 1080
    assert ytdlp._bucket(1920, 1080) == 1080      # 竖屏
    assert ytdlp._bucket(1088) == 1080
    assert ytdlp._bucket(818, 1920) == 1080       # 21:9 电影 1920x818 → 1080 档
    assert ytdlp._bucket(2160) == 2160


def test_progress_tracker():
    t = ytdlp.ProgressTracker()
    t.set_formats(["137", "140"], [90_000_000, 10_000_000])
    t.update("137", 45_000_000, 90_000_000)
    assert abs(t.fraction() - 0.45) < 1e-6
    t.update("137", 90_000_000, 90_000_000)
    t.update("140", 5_000_000, 10_000_000)
    assert abs(t.fraction() - 0.95) < 1e-6
    # 大小未知时按 85/15 估算，不会倒退
    t2 = ytdlp.ProgressTracker()
    t2.set_formats(["v", "a"], [None, None])
    t2.update("v", 100, 100)
    assert abs(t2.fraction() - 0.85) < 1e-6


def test_explain_error():
    assert "Deno" in ytdlp.explain_error("WARNING: No supported JavaScript runtime could be found")
    assert "代理" in ytdlp.explain_error("ERROR: Unable to download webpage: <urlopen error timed out>")
    assert ytdlp.explain_error("ERROR: something odd") == "something odd"


# ---------- 用假 yt-dlp 跑一遍完整下载流程（进度、合并、最终路径） ----------
FAKE_YTDLP = r'''
import sys, time
sys.stdout.reconfigure(encoding="utf-8")   # 和真 yt-dlp 的 --encoding utf-8 一致（Windows 默认编码不是 UTF-8）
args = sys.argv[1:]
if "-J" in args:
    print('{"id":"abc","title":"测试视频","uploader":"up","duration":61,"thumbnail":"http://t/1.jpg",'
          '"formats":[{"format_id":"137","vcodec":"avc1.640028","acodec":"none","height":1080,"width":1920,"fps":30,"filesize":90000000},'
          '{"format_id":"22","vcodec":"avc1","acodec":"mp4a","height":720,"width":1280,"fps":60,"filesize":40000000},'
          '{"format_id":"140","vcodec":"none","acodec":"mp4a.40.2","filesize":10000000}]}')
    sys.exit(0)
print('SYF|["137", "140"]|[90000000, 10000000]|[null, null]|137+140|NA', flush=True)
for i in range(1, 4):
    print(f"SYP|downloading|{i*30000000}|90000000|NA|5000000|3|137", flush=True)
for i in range(1, 3):
    print(f"SYP|downloading|{i*5000000}|10000000|NA|5000000|1|140", flush=True)
print("[Merger] Merging formats into \"out.mp4\"", flush=True)
print("SYD|C:/Videos/测试视频 [abc].mp4", flush=True)
'''


def test_fake_ytdlp_flow(tmp_path, monkeypatch):
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(FAKE_YTDLP, "utf-8")
    from app import components
    monkeypatch.setattr(components, "find", lambda n: str(script) if n == "yt-dlp" else None)
    # 让子进程用 python 跑假脚本
    real_base = ytdlp.base_args
    monkeypatch.setattr(ytdlp, "base_args", lambda p, s: [sys.executable] + real_base(p, s))
    settings = {"proxy_mode": "none", "proxy_platforms": {}, "cookies": {"youtube": {"mode": "none"}, "bilibili": {"mode": "none"}},
                "compat_mode": True, "container": "mp4", "filename_template": "%(title)s.%(ext)s"}
    info = ytdlp.probe("https://youtu.be/abc", "youtube", settings)
    assert [q["key"] for q in info["qualities"]] == ["1080", "720", "audio_m4a", "audio_mp3"]
    assert info["qualities"][1]["sub"] == "60帧"
    assert info["qualities"][0]["size"] == 100_000_000

    updates = []
    task = {"url": "https://youtu.be/abc", "platform": "youtube", "quality": "1080", "count": 1}
    path = ytdlp.download(task, settings, tmp_path, updates.append, threading.Event())
    assert path == "C:/Videos/测试视频 [abc].mp4"
    progs = [u["progress"] for u in updates if u["status"] == "downloading"]
    assert progs == sorted(progs), "进度不应倒退"
    assert abs(progs[-1] - 1.0) < 1e-6
    assert updates[-1]["status"] == "merging"



# ---------- 多连接分块下载 ----------
def _range_server(data: bytes, ranges=True):
    import http.server
    import re as _re

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            m = _re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", "")) if ranges else None
            if m:
                a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else len(data) - 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                body = data[a:b + 1]
            else:
                self.send_response(200)
                body = data
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v.mp4"


def test_fastdl_multi_and_single(tmp_path):
    import os
    from app import fastdl
    data = os.urandom(5 * 1024 * 1024 + 12345)   # 不是整块数，测边界
    for ranges in (True, False):
        srv, url = _range_server(data, ranges)
        try:
            total, ok, ctype, _ = fastdl.probe(url)
            assert total == len(data) and ok == ranges and ctype == "video/mp4"
            meter = fastdl.Meter()
            dest = tmp_path / f"out_{ranges}.mp4"
            fastdl.download(url, dest, connections=8, cancel=threading.Event(), meter=meter)
            assert dest.read_bytes() == data
            assert meter.done == len(data)
            assert not (tmp_path / f"out_{ranges}.mp4.part").exists()
        finally:
            srv.shutdown()


def test_fastdl_cancel(tmp_path):
    import os
    from app import fastdl
    srv, url = _range_server(os.urandom(4 * 1024 * 1024))
    ev = threading.Event()
    ev.set()
    try:
        fastdl.download(url, tmp_path / "c.mp4", connections=4, cancel=ev)
        assert False, "应该被取消"
    except fastdl.Canceled:
        pass
    finally:
        srv.shutdown()
    assert list(tmp_path.iterdir()) == []
