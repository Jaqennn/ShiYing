"""用真的 ComponentManager / Assets / SetupManager 走一遍安装（下载地址换成本机服务器）。

test_setup.py 用的是假的管理器，测不到真实安装代码里的错误；这里补上。
"""
import hashlib
import http.server
import io
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import components, fetch, setup, subtitle  # noqa: E402


class Settings(dict):
    def update(self, patch):
        super().update(patch)


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class _Files(http.server.SimpleHTTPRequestHandler):
    slow = False   # 测进度条时把下载限到每秒 800 KB 左右

    def log_message(self, *a):
        pass

    def copyfile(self, source, outputfile):
        if not self.slow:
            return super().copyfile(source, outputfile)
        while chunk := source.read(16384):
            outputfile.write(chunk)
            time.sleep(0.02)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    srv = tmp_path / "srv"
    payload = {
        "yt-dlp.exe": os.urandom(1_200_000),
        "ffmpeg.zip": _zip({"ffmpeg-master/bin/ffmpeg.exe": os.urandom(800_000),
                            "ffmpeg-master/bin/ffprobe.exe": os.urandom(400_000),
                            "ffmpeg-master/LICENSE.txt": b"GPL"}),
        "deno.zip": _zip({"deno.exe": os.urandom(1_100_000)}),
        "whisper.zip": _zip({"Release/whisper-cli.exe": os.urandom(900_000),
                             "Release/whisper.dll": os.urandom(300_000)}),
    }
    for name, data in payload.items():
        (srv / name).parent.mkdir(parents=True, exist_ok=True)
        (srv / name).write_bytes(data)
    model = os.urandom(10_200_000)
    vad = os.urandom(150_000)
    mfile = subtitle.MODELS["small.en"]["file"]
    for rel, data in ((f"{subtitle.MODEL_REPO}/resolve/main/{mfile}", model),
                      (f"{subtitle.VAD['repo']}/resolve/main/{subtitle.VAD['file']}", vad)):
        (srv / rel).parent.mkdir(parents=True, exist_ok=True)
        (srv / rel).write_bytes(data)

    class Quiet(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass

    httpd = Quiet(("127.0.0.1", 0), lambda *a, **kw: _Files(*a, directory=str(srv), **kw))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    bin_dir = tmp_path / "bin"
    models = tmp_path / "models"

    def find(n):
        p = bin_dir / (n + ".exe")
        return str(p) if p.exists() else None

    digests = {"yt-dlp.exe": "yt-dlp.exe", "ffmpeg-master-latest-win64-gpl.zip": "ffmpeg.zip",
               "deno-x86_64-pc-windows-msvc.zip": "deno.zip"}
    monkeypatch.setattr(components, "IS_WIN", True)
    monkeypatch.setattr(components, "BIN_DIR", bin_dir)
    monkeypatch.setattr(components, "find", find)
    monkeypatch.setattr(components, "exe", lambda n: n + ".exe")
    monkeypatch.setattr(components, "version", lambda n: "1.0" if find(n) else None)
    monkeypatch.setattr(components, "github_proxy", lambda s: None)
    monkeypatch.setattr(fetch, "release_digest",
                        lambda repo, tag, asset, proxy=None, timeout=12:
                        hashlib.sha256(payload[digests[asset]]).hexdigest())
    for name, file in (("yt-dlp", "yt-dlp.exe"), ("ffmpeg", "ffmpeg.zip"), ("deno", "deno.zip")):
        monkeypatch.setitem(components.SOURCES[name], "url", f"{base}/{file}")

    monkeypatch.setattr(subtitle, "IS_WIN", True)
    monkeypatch.setattr(subtitle, "BIN_DIR", bin_dir)
    monkeypatch.setattr(subtitle, "WHISPER_DIR", bin_dir / "whisper")
    monkeypatch.setattr(subtitle, "MODELS_DIR", models)
    monkeypatch.setattr(subtitle, "HF", {"official": base, "mirror": base})
    monkeypatch.setattr(subtitle, "github_proxy", lambda s: None)
    monkeypatch.setattr(subtitle, "has_nvidia", lambda: False)
    whisper_digest = hashlib.sha256(payload["whisper.zip"]).hexdigest()
    monkeypatch.setattr(subtitle.Assets, "_pick_asset",
                        lambda self, gpu: ("whisper-bin-x64.zip", f"{base}/whisper.zip", "v9.9.9", whisper_digest))

    settings = Settings(github_mirror="", hf_source="auto")
    yield {"base": base, "bin": bin_dir, "models": models, "payload": payload, "model": model,
           "settings": settings}
    httpd.shutdown()


def test_components_install_for_real(env):
    cm = components.ComponentManager(env["settings"])
    assert cm.missing() == ["yt-dlp", "ffmpeg", "deno"]
    assert cm.install("yt-dlp") is True, cm.get("yt-dlp")
    assert (env["bin"] / "yt-dlp.exe").read_bytes() == env["payload"]["yt-dlp.exe"]
    assert cm.get("yt-dlp")["status"] == "done"
    assert cm.install("ffmpeg") is True, cm.get("ffmpeg")
    assert (env["bin"] / "ffmpeg.exe").exists() and (env["bin"] / "ffprobe.exe").exists()
    assert not (env["bin"] / "LICENSE.txt").exists()
    assert cm.install_async("deno") is True
    end = time.time() + 20
    while cm.get("deno")["status"] == "working" and time.time() < end:
        time.sleep(0.05)
    assert cm.get("deno")["status"] == "done", cm.get("deno")
    assert cm.missing() == []
    assert not list(env["bin"].glob("*-download*")), "临时文件没删"


def test_component_bad_digest_reports_error(env, monkeypatch):
    monkeypatch.setattr(fetch, "release_digest", lambda *a, **kw: "0" * 64)
    cm = components.ComponentManager(env["settings"])
    assert cm.install("deno") is False
    st = cm.get("deno")
    assert st["status"] == "error" and "校验" in st["message"]
    assert not (env["bin"] / "deno.exe").exists()


def test_engine_and_model_install_for_real(env):
    a = subtitle.Assets(env["settings"])
    assert a.install_engine(False) is True, a.get("engine")
    assert (env["bin"] / "whisper" / "whisper-cli.exe").exists()
    assert "v9.9.9" in (env["bin"] / "whisper" / "BUILD.txt").read_text("utf-8")
    assert a.install_model("small.en") is True, a.get("model:small.en")
    assert subtitle.model_path("small.en").read_bytes() == env["model"]
    assert (env["models"] / subtitle.VAD["file"]).exists()
    st = a.status()
    assert st["engine"]["installed"] and st["vad"]["installed"]
    assert next(m for m in st["models"] if m["key"] == "small.en")["installed"]


def test_setup_wizard_with_real_managers(env):
    cm = components.ComponentManager(env["settings"])
    a = subtitle.Assets(env["settings"])
    mgr = setup.SetupManager(env["settings"], cm, a)
    plan = mgr.plan()
    assert plan["missing"] == ["yt-dlp", "ffmpeg", "deno"]
    assert plan["recommended_model"] == "model:small.en"
    mgr.start([*plan["missing"], "engine", "model:small.en"])
    end = time.time() + 60
    while mgr.status()["status"] == "working" and time.time() < end:
        time.sleep(0.05)
    st = mgr.status()
    assert st["status"] == "done", st
    assert all(it["status"] == "done" for it in st["items"]), st["items"]
    assert st["done"] >= st["total"] * 0.9
    assert mgr.plan()["missing"] == []


def test_wizard_shows_live_progress(env, monkeypatch):
    """下载过程中总进度、速度、剩余时间都要动起来（安装向导的进度条靠这些）。"""
    monkeypatch.setattr(_Files, "slow", True)
    cm = components.ComponentManager(env["settings"])
    mgr = setup.SetupManager(env["settings"], cm, subtitle.Assets(env["settings"]))
    mgr.start(["yt-dlp"])
    seen = []
    end = time.time() + 30
    while mgr.status()["status"] == "working" and time.time() < end:
        seen.append(mgr.status())
        time.sleep(0.05)
    assert mgr.status()["status"] == "done", mgr.status()
    mid = [s for s in seen if 0 < s["done"] < s["total"]]
    assert mid, "下载过程中总进度没动"
    assert any(s["speed"] > 0 for s in mid) and any(s["eta"] for s in mid)
    assert any("MB" in s["items"][0]["message"] for s in mid)
    assert all(s["current"] == "yt-dlp" and s["index"] == 1 for s in mid)
