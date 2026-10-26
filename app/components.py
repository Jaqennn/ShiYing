"""外部组件管理：yt-dlp / ffmpeg / deno 的查找、版本检测、一键下载与更新。"""
import io
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import requests

from .netutil import NO_WINDOW, github_proxy
from .paths import BIN_DIR, IS_WIN, bin_dirs, exe

GH = "https://github.com"
SOURCES = {
    "yt-dlp": {
        "url": f"{GH}/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        "kind": "exe",
        "desc": "下载引擎（YouTube / B站 / X）",
    },
    "ffmpeg": {
        "url": f"{GH}/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "kind": "zip",
        "members": ["ffmpeg.exe", "ffprobe.exe"],
        "desc": "合并音视频、转音频",
    },
    "deno": {
        "url": f"{GH}/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
        "kind": "zip",
        "members": ["deno.exe"],
        "desc": "YouTube 解签名所需的 JS 运行时",
    },
}

_VERSION_ARGS = {"yt-dlp": ["--version"], "ffmpeg": ["-version"], "deno": ["--version"]}


def find(name: str):
    for d in bin_dirs():
        p = d / exe(name)
        if p.exists():
            return str(p)
    return shutil.which(name)


def version(name: str):
    path = find(name)
    if not path:
        return None
    try:
        out = subprocess.run([path, *_VERSION_ARGS[name]], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15,
                             creationflags=NO_WINDOW).stdout.strip().splitlines()
        if not out:
            return "未知版本"
        line = out[0]
        if name == "ffmpeg":  # "ffmpeg version N-12345-g... Copyright"
            parts = line.split()
            return parts[2] if len(parts) > 2 else line
        if name == "deno":  # "deno 2.x.x (stable, release, ...)"
            return line.split()[1] if len(line.split()) > 1 else line
        return line
    except Exception:
        return "无法运行"


class ComponentManager:
    def __init__(self, settings):
        self.settings = settings
        self.state = {n: {"status": "idle", "progress": 0.0, "message": ""} for n in SOURCES}
        self._lock = threading.Lock()

    # ---------- 查询 ----------
    def status(self):
        out = []
        for name, meta in SOURCES.items():
            path = find(name)
            out.append({
                "name": name,
                "desc": meta["desc"],
                "path": path,
                "local": bool(path and Path(path).parent in bin_dirs()),
                "version": version(name) if path else None,
                **self.state[name],
            })
        return out

    def missing(self):
        return [n for n in SOURCES if not find(n)]

    # ---------- 安装 / 更新 ----------
    def _url(self, name):
        url = SOURCES[name]["url"]
        mirror = (self.settings["github_mirror"] or "").strip()
        if mirror:
            url = mirror.rstrip("/") + "/" + url
        return url

    def _set(self, name, **kw):
        with self._lock:
            self.state[name].update(kw)

    def install_async(self, name):
        if name not in SOURCES:
            return False
        if self.state[name]["status"] == "working":
            return True
        self._set(name, status="working", progress=0.0, message="准备下载…")
        threading.Thread(target=self._install, args=(name,), daemon=True).start()
        return True

    def _install(self, name):
        try:
            if not IS_WIN:
                raise RuntimeError("自动安装只支持 Windows，其他系统请用包管理器安装")
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            # yt-dlp 已存在时走自更新，速度更快
            if name == "yt-dlp" and (BIN_DIR / "yt-dlp.exe").exists():
                self._self_update_ytdlp()
                return
            data = self._download(name)
            meta = SOURCES[name]
            if meta["kind"] == "exe":
                self._atomic_write(BIN_DIR / exe(name), data)
            else:
                self._set(name, message="解压中…")
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for member in meta["members"]:
                        hit = next((i for i in zf.namelist() if i.replace("\\", "/").endswith("/" + member) or i == member), None)
                        if not hit:
                            raise RuntimeError(f"压缩包里没找到 {member}")
                        self._atomic_write(BIN_DIR / member, zf.read(hit))
            self._set(name, status="done", progress=1.0, message="已安装 " + (version(name) or ""))
        except Exception as e:
            self._set(name, status="error", message=_friendly(e))

    def _download(self, name) -> bytes:
        proxy = github_proxy(self.settings)
        proxies = {"http": proxy, "https": proxy} if proxy else None
        s = requests.Session()
        s.trust_env = False
        with s.get(self._url(name), stream=True, timeout=30, proxies=proxies,
                   headers={"User-Agent": "ShiYing"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            buf = io.BytesIO()
            done, last = 0, 0.0
            for chunk in r.iter_content(256 * 1024):
                buf.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last > 0.3:
                    last = now
                    mb = done / 1048576
                    msg = f"{mb:.1f} / {total / 1048576:.1f} MB" if total else f"{mb:.1f} MB"
                    self._set(name, progress=(done / total) if total else 0.0, message=msg)
            return buf.getvalue()

    @staticmethod
    def _atomic_write(path: Path, data: bytes):
        tmp = path.with_suffix(path.suffix + ".new")
        tmp.write_bytes(data)
        tmp.replace(path)

    def _self_update_ytdlp(self):
        self._set("yt-dlp", message="检查更新…")
        cmd = [find("yt-dlp"), "-U"]
        proxy = github_proxy(self.settings)
        if proxy:
            cmd += ["--proxy", proxy]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, creationflags=NO_WINDOW)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            raise RuntimeError(out.splitlines()[-1] if out else "更新失败")
        msg = "已是最新 " if "up to date" in out.lower() else "已更新到 "
        self._set("yt-dlp", status="done", progress=1.0, message=msg + (version("yt-dlp") or ""))


def _friendly(e: Exception) -> str:
    s = str(e)
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return "连不上 GitHub：请开代理，或在设置里填 GitHub 加速前缀"
    if isinstance(e, PermissionError):
        return "没有写入权限：把程序放到非系统目录（如 D:\\Tools\\拾影）再试"
    return s[:200]
