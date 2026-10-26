"""设置：JSON 持久化，缺省值与旧配置深度合并，升级后不丢字段。"""
import copy
import json
import threading

from .paths import DATA_DIR, default_download_dir

PLATFORMS = ["youtube", "bilibili", "x", "douyin", "other"]

DEFAULTS = {
    "download_dir": default_download_dir(),
    "split_by_platform": True,          # 按平台分子文件夹
    "filename_template": "%(title).60s [%(id)s].%(ext)s",
    "compat_mode": True,                # 兼容优先：H.264 + AAC，Windows 自带播放器直接能放
    "container": "mp4",                 # mp4 / mkv
    "default_quality": "1080",          # best / 2160 / 1440 / 1080 / 720 / 480 / audio_m4a / audio_mp3
    "max_concurrent": 2,
    "accelerate": True,                 # 多连接加速（类似 IDM）
    "connections": 16,                  # 每个文件的连接数
    "fragments": 8,                     # m3u8/DASH 分片并发数
    "proxy_mode": "system",             # system / manual / none
    "proxy_url": "http://127.0.0.1:7890",
    "proxy_platforms": {"youtube": True, "x": True, "bilibili": False, "douyin": False, "other": True,
                        "translate": False},
    # 每个平台的 Cookie：none / file(cookies.txt) / browser(firefox 等)
    "cookies": {p: {"mode": "none", "file": "", "browser": "firefox"} for p in PLATFORMS},
    "douyin_cookie": "",                # 抖音兜底：浏览器里复制的 Cookie 字符串
    "github_mirror": "",                # 组件下载加速前缀，例如 https://ghfast.top/
    "watch_clipboard": True,            # 切回窗口时识别剪贴板里的链接
    "theme": "system",                  # system / dark / light
    # ---- 字幕 ----
    "sub_model": "large-v3-turbo",      # base.en / small.en / large-v3-turbo
    "sub_vad": True,                    # 过滤静音和纯音乐段，减少胡乱识别
    "sub_translator": "deepl",          # deepl / llm
    "deepl_key": "",
    "llm_base_url": "https://api.deepseek.com",
    "llm_key": "",
    "llm_model": "deepseek-chat",
    "sub_outputs": {"zh": True, "bilingual": True, "en": False},
    "sub_auto": False,                  # 下载完自动生成字幕（解析页的勾选框默认值）
    "sub_prefer_platform": True,        # YouTube 有人工英文字幕时直接用
    "hf_source": "auto",                # 模型下载源：auto / mirror / official
}

_FILE = DATA_DIR / "settings.json"
_lock = threading.Lock()


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


class Settings:
    def __init__(self):
        self._data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        try:
            raw = json.loads(_FILE.read_text("utf-8"))
            self._data = _merge(DEFAULTS, raw)
        except FileNotFoundError:
            pass
        except Exception:
            # 配置损坏时备份后用默认值
            try:
                _FILE.rename(_FILE.with_suffix(".broken.json"))
            except Exception:
                pass

    def save(self):
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(_FILE)

    def all(self) -> dict:
        return copy.deepcopy(self._data)

    def update(self, patch: dict):
        self._data = _merge(self._data, patch)
        try:
            self._data["max_concurrent"] = max(1, min(5, int(self._data["max_concurrent"])))
        except Exception:
            self._data["max_concurrent"] = 2
        self.save()

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)
