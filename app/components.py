"""外部组件管理：yt-dlp / ffmpeg / deno 的查找、版本检测、一键下载与更新。"""
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import requests

from . import fetch
from .netutil import NO_WINDOW, github_proxy
from .paths import BIN_DIR, IS_WIN, bin_dirs, exe

GH = "https://github.com"
# approx 是大约的下载大小（字节），安装向导里先给用户一个数；真正的大小以下载时为准
SOURCES = {
    "yt-dlp": {
        "url": f"{GH}/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        "kind": "exe",
        "desc": "下载引擎（YouTube / B站 / X）",
        "repo": "yt-dlp/yt-dlp", "tag": "latest-release", "asset": "yt-dlp.exe",
        "approx": 23_600_000,
    },
    "ffmpeg": {
        "url": f"{GH}/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "kind": "zip",
        "members": ["ffmpeg.exe", "ffprobe.exe"],
        "desc": "合并音视频、转音频",
        "repo": "yt-dlp/FFmpeg-Builds", "tag": "latest", "asset": "ffmpeg-master-latest-win64-gpl.zip",
        "approx": 150_000_000,
    },
    "deno": {
        "url": f"{GH}/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
        "kind": "zip",
        "members": ["deno.exe"],
        "desc": "YouTube 解签名所需的 JS 运行时",
        "repo": "denoland/deno", "tag": "latest-release", "asset": "deno-x86_64-pc-windows-msvc.zip",
        "approx": 45_000_000,
    },
}

_VERSION_ARGS = {"yt-dlp": ["--version"], "ffmpeg": ["-version"], "deno": ["--version"]}
YTDLP_UPDATE_EVERY = 2 * 24 * 3600   # yt-dlp 自动检查更新的间隔


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


def _idle():
    return {"status": "idle", "progress": 0.0, "message": "", "done": 0, "total": 0, "speed": 0}


class ComponentManager:
    def __init__(self, settings):
        self.settings = settings
        self.state = {n: _idle() for n in SOURCES}
        self._stop = {n: threading.Event() for n in SOURCES}
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
                "approx": meta["approx"],
                **self.get(name),
            })
        return out

    def get(self, name):
        with self._lock:
            return dict(self.state[name])

    def missing(self):
        return [n for n in SOURCES if not find(n)]

    def installed(self, name):
        return bool(find(name))

    # ---------- 安装 / 更新 ----------
    def _url(self, name):
        return fetch.with_mirror(SOURCES[name]["url"], self.settings["github_mirror"])

    def _set(self, name, **kw):
        with self._lock:
            self.state[name].update(kw)

    def install_async(self, name):
        if name not in SOURCES:
            return False
        if self.get(name)["status"] == "working":
            return True
        self._set(name, **_idle(), status="working", message="准备下载…")
        threading.Thread(target=self.install, args=(name,), daemon=True).start()
        return True

    def stop(self, name):
        if name in self._stop:
            self._stop[name].set()

    def install(self, name):
        """装（或更新）一个组件，装完返回 True。出错时状态里写明原因并返回 False。"""
        stop = self._stop[name]
        stop.clear()
        self._set(name, **_idle(), status="working", message="准备下载…")
        try:
            if not IS_WIN:
                raise RuntimeError("自动安装只支持 Windows，其他系统请用包管理器安装")
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            # yt-dlp 已存在时走自更新，速度更快
            if name == "yt-dlp" and (BIN_DIR / "yt-dlp.exe").exists():
                self._self_update_ytdlp()
                return True
            meta = SOURCES[name]
            tmp = BIN_DIR / (f"{name}-download.zip" if meta["kind"] == "zip" else f"{name}-download.exe")
            self._fetch(name, tmp, stop)
            try:
                if meta["kind"] == "exe":
                    tmp.replace(BIN_DIR / exe(name))
                else:
                    self._set(name, message="解压中…")
                    with zipfile.ZipFile(tmp) as zf:
                        for member in meta["members"]:
                            hit = next((i for i in zf.namelist()
                                        if i.replace("\\", "/").endswith("/" + member) or i == member), None)
                            if not hit:
                                raise RuntimeError(f"压缩包里没找到 {member}")
                            _atomic_write(BIN_DIR / member, zf.read(hit))
            finally:
                tmp.unlink(missing_ok=True)
            self._set(name, status="done", progress=1.0, speed=0, message="已安装 " + (version(name) or ""))
            return True
        except fetch.Stopped:
            self._set(name, **_idle(), message="已停止")
            return False
        except Exception as e:
            self._set(name, status="error", speed=0, message=_friendly(e))
            return False

    def _fetch(self, name, dest, stop):
        meta = SOURCES[name]
        mirror = (self.settings["github_mirror"] or "").strip()
        # 走镜像时不用代理；直连 GitHub 时用系统/手动代理
        proxy = None if mirror else github_proxy(self.settings)
        self._set(name, message="查询官方校验值…")
        digest = fetch.release_digest(meta["repo"], meta["tag"], meta["asset"], github_proxy(self.settings))
        self._set(name, message="连接 " + ("加速镜像" if mirror else "GitHub") + "…")

        def on_bytes(done, total, speed):
            msg = f"{done / 1048576:.1f} / {total / 1048576:.1f} MB" if total else f"{done / 1048576:.1f} MB"
            self._set(name, done=done, total=total, speed=speed,
                      progress=(done / total) if total else 0.0, message=msg)

        fetch.download(self._url(name), dest, proxy=proxy, stop=stop, on_bytes=on_bytes, digest=digest,
                       expect_min=1_000_000)

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

    def auto_update_ytdlp(self, busy=lambda: False):
        """启动时悄悄检查 yt-dlp 更新：只更新拾影自己装的那份，两天最多查一次，失败也不打扰用户。"""
        if not self.settings.get("auto_update_ytdlp", True):
            return False
        if not (BIN_DIR / exe("yt-dlp")).exists():
            return False
        if time.time() - float(self.settings.get("ytdlp_checked_at") or 0) < YTDLP_UPDATE_EVERY:
            return False
        if busy() or self.get("yt-dlp")["status"] == "working":
            return False
        self.settings.update({"ytdlp_checked_at": time.time()})
        self._set("yt-dlp", status="working", message="检查更新…")
        try:
            self._self_update_ytdlp()
            return True
        except Exception:
            self._set("yt-dlp", **_idle())
            return False


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_bytes(data)
    tmp.replace(path)


def _friendly(e: Exception) -> str:
    s = str(e)
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return "连不上 GitHub：可以换加速镜像，或者开代理后再试"
    if isinstance(e, PermissionError):
        return "没有写入权限：把程序放到非系统目录（如 D:\\Tools\\拾影）再试"
    if "HTTP 404" in s:
        return "GitHub 上找不到这个文件（可能刚好在更新），过几分钟再试"
    return s[:200]
