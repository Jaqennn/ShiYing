"""代理解析、剪贴板、打开文件夹等系统相关的小工具。"""
import os
import subprocess
import sys
from pathlib import Path

from .paths import IS_WIN


def system_proxy():
    """读取 Windows 系统代理（Clash / v2rayN 开了“系统代理”就会写这里）。"""
    if IS_WIN:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            server = (server or "").strip()
            if not server:
                return None
            if "=" in server:  # "http=127.0.0.1:7890;https=127.0.0.1:7890;socks=..."
                parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
                server = parts.get("https") or parts.get("http") or ""
                if not server and parts.get("socks"):
                    return "socks5://" + parts["socks"]
            if not server:
                return None
            if "://" not in server:
                server = "http://" + server
            return server
        except Exception:
            return None
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")


def resolve_proxy(settings, platform: str):
    mode = settings["proxy_mode"]
    if mode == "none":
        return None
    if not settings["proxy_platforms"].get(platform, False):
        return None
    if mode == "manual":
        return (settings["proxy_url"] or "").strip() or None
    return system_proxy()


def github_proxy(settings):
    """下载组件（GitHub）用的代理：手动代理优先，否则系统代理。"""
    if settings["proxy_mode"] == "manual" and settings["proxy_url"]:
        return settings["proxy_url"]
    if settings["proxy_mode"] == "none":
        return None
    return system_proxy()


def read_clipboard() -> str:
    if IS_WIN:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        CF_UNICODETEXT = 13
        if not user32.OpenClipboard(None):
            return ""
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = kernel32.GlobalLock(h)
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()
    for cmd in (["pbpaste"], ["xclip", "-o", "-selection", "clipboard"], ["wl-paste"]):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
        except Exception:
            continue
    return ""


def reveal(path: str):
    """在资源管理器中显示文件（选中它）；传目录则直接打开。"""
    p = Path(path)
    if IS_WIN:
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p if p.exists() else p.parent))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])


def open_file(path: str):
    if IS_WIN:
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# Windows 下启动子进程时不弹黑色控制台窗口
NO_WINDOW = 0x08000000 if IS_WIN else 0


def kill_tree(proc: subprocess.Popen):
    """结束进程树（yt-dlp 会再拉起 ffmpeg）。"""
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
