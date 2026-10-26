"""暴露给前端（window.pywebview.api）的接口。每个方法都返回 {ok, data | error}。"""
import functools
import os
import sys
import traceback
from pathlib import Path

from . import APP_NAME, __version__, detect
from .components import ComponentManager
from .netutil import open_file, read_clipboard, reveal, system_proxy
from .paths import BIN_DIR, DATA_DIR
from .settings import Settings
from .tasks import TaskManager
from . import subtitle


def api(fn):
    @functools.wraps(fn)
    def wrap(self, *a, **kw):
        try:
            return {"ok": True, "data": fn(self, *a, **kw)}
        except Exception as e:  # 统一把错误转成中文提示给前端
            if os.environ.get("SHIYING_DEBUG"):
                traceback.print_exc()
            return {"ok": False, "error": str(e) or e.__class__.__name__}
    return wrap


def _dialog(kind):
    """兼容新旧版 pywebview 的对话框常量。"""
    import webview
    fd = getattr(webview, "FileDialog", None)
    if fd is not None:
        return getattr(fd, kind)
    return getattr(webview, f"{kind}_DIALOG")


class Api:
    def __init__(self):
        self._window = None
        self._settings = Settings()
        self._tasks = TaskManager(self._settings)
        self._comps = ComponentManager(self._settings)
        self._assets = subtitle.Assets(self._settings)
        self._subs = subtitle.SubtitleManager(self._settings, self._tasks)
        self._tasks.on_done = self._subs.enqueue

    def bind(self, window):
        self._window = window

    # ---------- 基本 ----------
    @api
    def app_info(self):
        return {"name": APP_NAME, "version": __version__, "data_dir": str(DATA_DIR),
                "bin_dir": str(BIN_DIR), "platform": sys.platform,
                "missing": self._comps.missing()}

    @api
    def detect(self, text):
        return detect.extract(text or "")

    @api
    def detect_all(self, text):
        return detect.extract_all(text or "")

    @api
    def clipboard(self):
        return read_clipboard() or ""

    # ---------- 解析与下载 ----------
    @api
    def parse(self, text):
        return self._tasks.parse(text)

    @api
    def add_task(self, probe_id, quality, auto_sub=False):
        return self._tasks.add(probe_id, quality, auto_sub)

    @api
    def tasks(self):
        return self._tasks.list()

    @api
    def cancel(self, tid):
        return self._tasks.cancel(tid)

    @api
    def retry(self, tid):
        return self._tasks.retry(tid)

    @api
    def remove(self, tid):
        return self._tasks.remove(tid)

    @api
    def clear_finished(self):
        return self._tasks.clear_finished()

    @api
    def open_task(self, tid):
        t = self._tasks.get(tid)
        if t and t.get("filepath") and Path(t["filepath"]).exists():
            open_file(t["filepath"])
            return True
        raise ValueError("文件不存在，可能已被移动或删除")

    @api
    def reveal_task(self, tid):
        t = self._tasks.get(tid)
        if t and t.get("filepath") and Path(t["filepath"]).exists():
            reveal(t["filepath"])
        else:
            self._open_download_dir()
        return True

    @api
    def open_download_dir(self):
        self._open_download_dir()
        return True

    def _open_download_dir(self):
        d = Path(self._settings["download_dir"]).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        reveal(str(d))

    # ---------- 设置 ----------
    @api
    def get_settings(self):
        s = self._settings.all()
        s["_system_proxy"] = system_proxy() or ""
        return s

    @api
    def save_settings(self, patch):
        patch = {k: v for k, v in (patch or {}).items() if not k.startswith("_")}
        self._settings.update(patch)
        return self._settings.all()

    @api
    def choose_folder(self):
        res = self._window.create_file_dialog(_dialog("FOLDER"),
                                              directory=self._settings["download_dir"])
        return res[0] if res else None

    @api
    def choose_cookie_file(self):
        res = self._window.create_file_dialog(
            _dialog("OPEN"), file_types=("Cookie 文件 (*.txt)", "所有文件 (*.*)"))
        return res[0] if res else None

    # ---------- 字幕 ----------
    @api
    def subtitle(self, tid):
        return self._subs.enqueue(tid)

    @api
    def cancel_subtitle(self, tid):
        return self._subs.cancel(tid)

    @api
    def add_local_videos(self):
        exts = "*.mp4;*.mkv;*.webm;*.mov;*.avi;*.flv;*.m4v;*.ts;*.mp3;*.m4a;*.wav"
        res = self._window.create_file_dialog(
            _dialog("OPEN"), allow_multiple=True,
            file_types=(f"视频和音频 ({exts})", "所有文件 (*.*)"))
        added = []
        for path in (res or []):
            t = self._tasks.add_local(path)
            try:
                self._subs.enqueue(t["id"])
            except Exception:
                pass
            added.append(t)
        return added

    @api
    def reveal_subtitle(self, tid):
        t = self._tasks.get(tid)
        files = (t or {}).get("sub_files") or []
        for f in files:
            if Path(f).exists():
                reveal(f)
                return True
        raise ValueError("字幕文件不存在，可能已被移动或删除")

    @api
    def sub_assets(self):
        return self._assets.status()

    @api
    def install_whisper(self, gpu=False):
        return self._assets.install_engine_async(bool(gpu))

    @api
    def install_model(self, key):
        return self._assets.install_model_async(key)

    @api
    def open_models_dir(self):
        subtitle.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        reveal(str(subtitle.MODELS_DIR))
        return True

    @api
    def check_translator(self):
        tr = subtitle.make_translator(self._settings)
        if isinstance(tr, subtitle.DeepL):
            u = tr.usage()
            return f"DeepL 可用：本月已用 {u['used']:,} / {u['limit']:,} 字符"
        return "大模型接口可用，试译：" + tr.check()

    # ---------- 组件 ----------
    @api
    def components(self):
        return self._comps.status()

    @api
    def install_component(self, name):
        return self._comps.install_async(name)

    @api
    def open_bin_dir(self):
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        reveal(str(BIN_DIR))
        return True
