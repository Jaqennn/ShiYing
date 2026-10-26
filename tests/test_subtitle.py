"""字幕流程测试：真 ffmpeg + 假 whisper-cli（输出格式照 whisper.cpp 源码）+ 假 DeepL / 大模型服务器。"""
import http.server
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import components, subtitle  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg"))

# whisper.cpp 的 JSON：制表符缩进，只转义引号和反斜杠（文本里可能出现原样的控制字符）
FAKE_WHISPER = r'''
import sys, time, os
a = sys.argv[1:]
of = a[a.index("-of") + 1]; wav = a[a.index("-f") + 1]; model = a[a.index("-m") + 1]
assert os.path.exists(wav), "wav missing: " + wav
assert os.path.exists(model), "model missing (cwd 不对?)"
assert all(ord(c) < 128 for c in " ".join(a)), "参数里不能有非 ASCII"
for p in (0, 25, 50, 75, 100):
    sys.stderr.write(f"whisper_print_progress_callback: progress = {p:3d}%\n"); sys.stderr.flush()
segs = [(0, 2400, " Hello everyone, and welcome back"), (2400, 5000, " to the channel."),
        (5000, 5000, " "), (5200, 8000, " Today we're testing \"subtitles\"\tand A&B <tags>."),
        (8000, 9500, " Today we're testing \"subtitles\"\tand A&B <tags>."), (9800, 12000, " Thanks for watching!")]
lang = "zh" if "zh" in os.environ.get("FAKE_LANG", "") else "en"
with open(of + ".json", "w", encoding="utf-8") as f:
    f.write('{\n\t"systeminfo": "AVX = 1",\n\t"result": {\n\t\t"language": "%s"\n\t},\n\t"transcription": [\n' % lang)
    rows = []
    for a0, b0, t in segs:
        t = t.replace("\\", "\\\\").replace('"', '\\"')
        rows.append('\t\t{\n\t\t\t"offsets": {\n\t\t\t\t"from": %d,\n\t\t\t\t"to": %d\n\t\t\t},\n\t\t\t"text": "%s"\n\t\t}' % (a0, b0, t))
    f.write(",\n".join(rows) + "\n\t]\n}\n")
'''


class FakeAPI(http.server.BaseHTTPRequestHandler):
    calls = []
    zh_hans_rejected = False

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v2/usage":
            return self._send(200, {"character_count": 1000, "character_limit": 500000})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAPI.calls.append((self.path, body, self.headers.get("Authorization")))
        if self.path == "/v2/translate":
            if not self.headers.get("Authorization", "").startswith("DeepL-Auth-Key "):
                return self._send(403, {"message": "Forbidden"})
            if body["target_lang"] == "ZH-HANS" and not FakeAPI.zh_hans_rejected:
                FakeAPI.zh_hans_rejected = True   # 模拟老接口：不认 ZH-HANS
                return self._send(400, {"message": "Value for 'target_lang' not supported."})
            out = []
            for t in body["text"]:
                if body.get("tag_handling") == "xml":
                    parts = re.findall(r'<s i="(\d+)">(.*?)</s>', t, re.S)
                    # 故意吞掉第二个标签，测试逐条补翻
                    out.append(" ".join(f'<s i="{i}">译:{x}</s>' for k, (i, x) in enumerate(parts) if k != 1))
                else:
                    out.append("补:" + t)
            return self._send(200, {"translations": [{"text": x} for x in out]})
        if self.path.endswith("/chat/completions"):
            user = json.loads(body["messages"][1]["content"])
            lines = user["待翻译"]
            if len(lines) > 2:   # 第一次故意返回条数不对，测试对半拆分重试
                return self._send(200, {"choices": [{"message": {"content": json.dumps({"t": ["少了"]})}}]})
            return self._send(200, {"choices": [{"message": {"content": "好的：" + json.dumps({"t": ["中:" + x for x in lines]}, ensure_ascii=False)}}]})
        self._send(404, {})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    FakeAPI.calls, FakeAPI.zh_hans_rejected = [], False
    monkeypatch.setenv("SHIYING_DEEPL_URL", base)

    models = tmp_path / "模型"          # 带中文的目录，验证参数里只有相对路径
    monkeypatch.setattr(subtitle, "MODELS_DIR", models)
    monkeypatch.setattr(subtitle, "WORK_DIR", models / "work")
    models.mkdir()
    (models / subtitle.MODELS["large-v3-turbo"]["file"]).write_bytes(b"x")
    fake = tmp_path / "fake_whisper.py"
    fake.write_text(FAKE_WHISPER, "utf-8")
    if sys.platform == "win32":
        wrapper = tmp_path / "whisper-cli.bat"
        wrapper.write_text(f'@"{sys.executable}" "{fake}" %*\r\n', "utf-8")
    else:
        wrapper = tmp_path / "whisper-cli"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        wrapper.chmod(0o755)
    monkeypatch.setattr(subtitle, "whisper_exe", lambda: str(wrapper))
    monkeypatch.setattr(components, "find", lambda n: shutil.which("ffmpeg") if n == "ffmpeg" else None)

    video = tmp_path / "视频" / "测试 视频 [abc].mp4"
    video.parent.mkdir()
    if HAS_FFMPEG:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=3", "-shortest", "-c:a", "aac", "-c:v", "libx264",
                        str(video)], check=True)
    settings = {"sub_model": "large-v3-turbo", "sub_vad": True, "sub_translator": "deepl", "deepl_key": "k:fx",
                "llm_base_url": base, "llm_key": "sk-test", "llm_model": "m",
                "sub_outputs": {"zh": True, "bilingual": True, "en": True}, "sub_prefer_platform": True,
                "proxy_mode": "none", "proxy_platforms": {}, "cookies": {}}
    yield {"video": video, "settings": settings, "base": base}
    srv.shutdown()


def _srt_blocks(path):
    text = Path(path).read_text("utf-8-sig")
    return [b.split("\n") for b in text.replace("\r\n", "\n").strip().split("\n\n")]


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_pipeline_deepl(env):
    stages = []
    task = {"filepath": str(env["video"]), "platform": "x", "url": "https://x.com/a/status/1"}
    res = subtitle.run(task, env["settings"], stages.append, threading.Event())
    assert res["lang"] == "en" and res["source"] == "whisper"
    names = sorted(Path(f).name for f in res["files"])
    assert names == ["测试 视频 [abc].en.srt", "测试 视频 [abc].zh-en.srt", "测试 视频 [abc].zh.srt"]

    zh = _srt_blocks(env["video"].with_name("测试 视频 [abc].zh.srt"))
    assert len(zh) == 4                     # 空行去掉、相邻重复合并
    assert zh[0][1] == "00:00:00,000 --> 00:00:02,400"
    assert zh[0][2] == "译:Hello everyone, and welcome back"
    assert zh[1][2] == "补:to the channel."  # 被吞掉的标签逐条补翻
    assert zh[2][2] == '译:Today we\'re testing "subtitles" and A&B <tags>.'   # 实体还原、制表符归一
    assert zh[2][1] == "00:00:05,200 --> 00:00:09,500"                      # 重复句合并后的时间
    bi = _srt_blocks(env["video"].with_name("测试 视频 [abc].zh-en.srt"))
    assert bi[0][2:] == ["译:Hello everyone, and welcome back", "Hello everyone, and welcome back"]

    xml_calls = [c for c in FakeAPI.calls if c[1].get("tag_handling") == "xml"]
    assert xml_calls[-1][1]["target_lang"] == "ZH" and xml_calls[-1][1]["source_lang"] == "EN"
    assert "&amp;" in xml_calls[-1][1]["text"][0] and "&lt;tags&gt;" in xml_calls[-1][1]["text"][0]
    st = [s["sub_status"] for s in stages]
    assert st[0] == "extracting" and "recognizing" in st and "translating" in st and st[-1] == "writing"
    rec = [s["sub_progress"] for s in stages if s["sub_status"] == "recognizing"]
    assert rec[-1] == 1.0
    assert list((subtitle.WORK_DIR).iterdir()) == []   # 临时 wav / json 已清理


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_pipeline_llm(env):
    s = dict(env["settings"], sub_translator="llm", sub_outputs={"zh": True, "bilingual": False, "en": False})
    res = subtitle.run({"filepath": str(env["video"]), "platform": "local"}, s, lambda d: None, threading.Event())
    zh = _srt_blocks(res["files"][0])
    assert [b[2] for b in zh] == ["中:Hello everyone, and welcome back", "中:to the channel.",
                                  '中:Today we\'re testing "subtitles" and A&B <tags>.', "中:Thanks for watching!"]
    urls = {c[0] for c in FakeAPI.calls}
    assert urls == {"/v1/chat/completions"}


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_pipeline_chinese_audio_skips_translation(env, monkeypatch):
    monkeypatch.setenv("FAKE_LANG", "zh")
    res = subtitle.run({"filepath": str(env["video"]), "platform": "local"}, env["settings"], lambda d: None, threading.Event())
    assert [Path(f).name for f in res["files"]] == ["测试 视频 [abc].zh.srt"]
    assert not FakeAPI.calls


def test_missing_setup_messages(env, monkeypatch):
    monkeypatch.setattr(subtitle, "whisper_exe", lambda: None)
    with pytest.raises(subtitle.SubtitleError, match="识别引擎"):
        subtitle.recognize(env["video"], env["settings"], lambda *a: None, threading.Event())
    with pytest.raises(subtitle.SubtitleError, match="DeepL 密钥"):
        subtitle.DeepL("")


def test_cancel_before_start(env):
    ev = threading.Event()
    ev.set()
    with pytest.raises(subtitle.Canceled):
        subtitle.run({"filepath": str(env["video"]), "platform": "local"}, env["settings"], lambda d: None, ev)


def test_parse_vtt_and_srt_time():
    vtt = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.500 align:start position:0%
<c>Hello</c> &amp; welcome

1:02:03.004 --> 1:02:05.000
second line
continues
"""
    segs = subtitle.parse_vtt(vtt)
    assert segs == [(1000, 3500, "Hello & welcome"), (3723004, 3725000, "second line continues")]
    assert subtitle.ms_to_srt(3723004) == "01:02:03,004"


def test_llm_url_rules():
    def u(base):
        return subtitle.LLM(base, "k", "m").url
    assert u("https://api.deepseek.com") == "https://api.deepseek.com/v1/chat/completions"
    assert u("https://api.moonshot.cn/v1/") == "https://api.moonshot.cn/v1/chat/completions"
    assert u("https://dashscope.aliyuncs.com/compatible-mode/v1") == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert u("https://x.com/v1/chat/completions") == "https://x.com/v1/chat/completions"


def test_queue_auto_and_local(tmp_path, monkeypatch):
    """下载完成自动排字幕、本地视频加字幕、取消排队中的任务。"""
    import time
    from app import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(tasks_mod, "DATA_DIR", tmp_path)
    gate = threading.Event()
    seen = []

    def fake_run(task, settings, on_update, cancel):
        seen.append(task["id"])
        on_update({"sub_status": "recognizing", "sub_progress": 0.5, "sub_msg": "识别语音 50%"})
        gate.wait(5)
        return {"files": [task["filepath"] + ".zh.srt"], "source": "whisper", "lang": "en", "count": 7}

    monkeypatch.setattr(subtitle, "run", fake_run)
    st = {"max_concurrent": 1}
    tm = tasks_mod.TaskManager(st)
    sm = subtitle.SubtitleManager(st, tm)
    tm.on_done = sm.enqueue

    v1, v2 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    v1.write_bytes(b"1")
    v2.write_bytes(b"2")
    t1 = tm.add_local(str(v1))
    t2 = tm.add_local(str(v2))
    assert tm.add_local(str(v1))["id"] == t1["id"]          # 同一个文件不重复加
    sm.enqueue(t1["id"])
    sm.enqueue(t2["id"])
    time.sleep(0.3)
    assert tm.get(t1["id"])["sub_status"] == "recognizing"
    assert tm.get(t2["id"])["sub_status"] == "queued"       # 一次只跑一个
    sm.cancel(t2["id"])
    assert tm.get(t2["id"])["sub_status"] == ""
    gate.set()
    time.sleep(0.3)
    t = tm.get(t1["id"])
    assert t["sub_status"] == "done" and t["sub_msg"] == "字幕已生成（7 条）" and t["sub_files"]
    assert seen == [t1["id"]]
    pub = [x for x in tm.list() if x["id"] == t1["id"]][0]
    assert pub["sub_files"] and "sub_progress" in pub
    with pytest.raises(subtitle.SubtitleError):
        tm.tasks["x"] = {"id": "x", "status": "downloading"}
        sm.enqueue("x")



def test_pick_whisper_asset_current_release_layout():
    """照 2026-10 GitHub 上的真实情况：v1.9.5 只有源码；b5454 新命名；b5130 旧命名。"""
    def rel(tag, names):
        return {"tag_name": tag, "draft": False,
                "assets": [{"name": n, "browser_download_url": f"https://github.com/x/releases/download/{tag}/{n}"} for n in names]}
    releases = [
        rel("v1.9.5", []),
        rel("b5454", ["whisper-b5454-xcframework.zip", "whisper-bin-win-cpu-arm64.zip", "whisper-bin-win-cuda-11.8.0-x64.zip",
                      "whisper-bin-win-cuda-12.4.0-x64.zip", "whisper-bin-win-cuda-13.4-arm64.zip", "whisper-bin-Win32.zip",
                      "whisper-bin-x64.zip", "whisper-blas-bin-Win32.zip", "whisper-blas-bin-x64.zip"]),
        rel("b5130", ["whisper-cublas-11.8.0-bin-x64.zip", "whisper-cublas-12.4.0-bin-x64.zip", "whisper-blas-bin-x64.zip"]),
    ]
    assert subtitle.pick_whisper_asset(releases, True)[:1] + (subtitle.pick_whisper_asset(releases, True)[2],) == \
        ("whisper-bin-win-cuda-12.4.0-x64.zip", "b5454")
    assert subtitle.pick_whisper_asset(releases, False)[0] == "whisper-blas-bin-x64.zip"
    assert subtitle.pick_whisper_asset(releases[2:], True)[0] == "whisper-cublas-12.4.0-bin-x64.zip"   # 旧命名
    only_arm = [rel("b1", ["whisper-bin-win-cuda-13.4-arm64.zip"])]
    assert subtitle.pick_whisper_asset(only_arm, True) is None                                          # 不会装成 arm64
    assert subtitle.pick_whisper_asset([rel("v9", [])], False) is None


def test_install_whisper_zip_keeps_old_on_failure(tmp_path):
    import zipfile as zf_mod
    target = tmp_path / "whisper"
    target.mkdir()
    (target / "whisper-cli.exe").write_bytes(b"old")
    bad = tmp_path / "bad.zip"
    with zf_mod.ZipFile(bad, "w") as z:
        z.writestr("Release/readme.txt", "no exe here")
    with pytest.raises(subtitle.SubtitleError):
        subtitle.install_whisper_zip(bad, target, "bad")
    assert (target / "whisper-cli.exe").read_bytes() == b"old"          # 旧引擎还在
    good = tmp_path / "good.zip"
    with zf_mod.ZipFile(good, "w") as z:
        z.writestr("Release/whisper-cli.exe", "new")
        z.writestr("Release/ggml-cuda.dll", "dll")
    subtitle.install_whisper_zip(good, target, "whisper-bin-win-cuda-12.4.0-x64.zip b5454")
    assert (target / "whisper-cli.exe").read_text() == "new" and (target / "ggml-cuda.dll").exists()
    assert (target / "BUILD.txt").read_text("utf-8").endswith("b5454")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.zip", "good.zip", "whisper"]   # 没有残留临时目录
