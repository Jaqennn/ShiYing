"""安装向导：把要装的组件列成一张清单，一键按顺序装完，汇总进度、速度和剩余时间。

组件本身的下载和安装还是 components.ComponentManager（yt-dlp / ffmpeg / deno）和
subtitle.Assets（识别引擎、模型）在做，这里只负责排队、汇总进度、停止和换镜像重来。
"""
import threading
import time

from . import components
from .paths import LOCAL_DIR

ESSENTIAL = ["yt-dlp", "ffmpeg", "deno"]
DEFAULT_MIRROR = "https://ghfast.top/"
SLOW_BPS = 200 * 1024   # 一项下载开始 SLOW_AFTER 秒后平均速度还低于这个数，就提示换加速镜像
SLOW_AFTER = 12
ENGINE_SIZE = {True: 685_000_000, False: 21_700_000}
DESCS = {
    "yt-dlp": "下载引擎：YouTube、哔哩哔哩、X 和上千个网站都靠它",
    "ffmpeg": "把画面和声音合成一个文件，也负责转音频",
    "deno": "YouTube 必需，用来解开视频地址",
}


class SetupManager:
    def __init__(self, settings, comps, assets):
        self.settings = settings
        self.comps = comps
        self.assets = assets
        self.lock = threading.Lock()
        self.job = None
        self._stop = threading.Event()
        self._restart = False
        self._thread = None

    # ---------- 清单 ----------
    def plan(self):
        comps = {c["name"]: c for c in self.comps.status()}
        a = self.assets.status()
        gpu = bool(a["nvidia"])
        rec = "large-v3-turbo" if gpu else "small.en"
        items = []
        for name in ESSENTIAL:
            c = comps[name]
            items.append({"key": name, "group": "download", "label": name, "desc": DESCS[name],
                          "size": c["approx"], "installed": bool(c["path"]), "version": c["version"] or ""})
        eng = a["engine"]
        items.append({"key": "engine", "group": "subtitle", "label": "识别引擎",
                      "desc": "GPU 版：检测到 NVIDIA 显卡，识别快很多" if gpu else "CPU 版：没检测到 NVIDIA 显卡",
                      "size": ENGINE_SIZE[gpu], "installed": bool(eng["installed"]), "gpu": gpu})
        for m in a["models"]:
            items.append({"key": "model:" + m["key"], "group": "model", "label": m["label"], "desc": m["desc"],
                          "size": m["size"], "installed": bool(m["installed"]), "recommended": m["key"] == rec})
        return {
            "items": items, "gpu": gpu, "recommended_model": "model:" + rec,
            "missing": [n for n in ESSENTIAL if not comps[n]["path"]],
            "location": str(LOCAL_DIR), "mirror": self.settings["github_mirror"] or "",
            "default_mirror": DEFAULT_MIRROR, "job": self.status(),
        }

    # ---------- 安装 ----------
    def start(self, keys):
        valid = set(ESSENTIAL) | {"engine"} | {"model:" + k for k in _model_keys(self.assets)}
        keys = [k for k in dict.fromkeys(keys or []) if k in valid]
        with self.lock:
            if self.job and self.job["status"] == "working":
                return self._view()
            if not keys:
                raise ValueError("没有要装的东西")
            # 先装下载组件，再装字幕引擎，最后是模型（体积最大）
            order = {k: i for i, k in enumerate([*ESSENTIAL, "engine"])}
            keys.sort(key=lambda k: order.get(k, 99))
            gpu = bool(self.assets.status()["nvidia"])
            self.job = {"status": "working", "gpu": gpu, "items": [self._item(k, gpu) for k in keys],
                        "started": time.time()}
            self._stop.clear()
            self._restart = False
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return self._view()

    def retry(self):
        """把没装上（失败或停止）的几项重新装一遍。"""
        with self.lock:
            if not self.job or self.job["status"] == "working":
                return self._view()
            todo = [it["key"] for it in self.job["items"] if it["status"] != "done"]
        return self.start(todo)

    def stop(self):
        with self.lock:
            if not self.job or self.job["status"] != "working":
                return self._view()
            self._stop.set()
            cur = self._current()
        if cur:
            self._stop_item(cur["key"])
        return self.status()

    def use_mirror(self, prefix=None):
        """换成 GitHub 加速镜像。正在从 GitHub 下的那一项停下来，用镜像从头再下。"""
        prefix = (prefix or DEFAULT_MIRROR).strip()
        if not prefix.startswith(("https://", "http://")):
            raise ValueError("加速前缀要以 https:// 开头")
        if not prefix.endswith("/"):
            prefix += "/"
        self.settings.update({"github_mirror": prefix})
        with self.lock:
            cur = self._current() if self.job and self.job["status"] == "working" else None
            if cur and _from_github(cur["key"]):
                self._restart = True
        if cur and _from_github(cur["key"]):
            self._stop_item(cur["key"])
        return self.status()

    def _item(self, key, gpu):
        if key in components.SOURCES:
            label, size = key, components.SOURCES[key]["approx"]
        elif key == "engine":
            label, size = "识别引擎（" + ("GPU 版" if gpu else "CPU 版") + "）", ENGINE_SIZE[gpu]
        else:
            m = next((m for m in self.assets.status()["models"] if "model:" + m["key"] == key), {})
            label, size = "识别模型 · " + m.get("label", key), m.get("size", 0)
        return {"key": key, "label": label, "size": size, "status": "waiting", "message": "",
                "done": 0, "total": 0, "progress": 0.0, "started": 0.0}

    def _current(self):
        return next((it for it in (self.job or {}).get("items", []) if it["status"] == "working"), None)

    def _stop_item(self, key):
        if key in components.SOURCES:
            self.comps.stop(key)
        else:
            self.assets.stop(key)

    def _install(self, key, gpu):
        if key in components.SOURCES:
            # 设置页可能正好在装同一个组件，等它装完再说
            while self.comps.get(key)["status"] == "working" and not self._stop.is_set():
                time.sleep(0.3)
            if self.comps.installed(key):
                return True
            return self.comps.install(key)
        if key == "engine":
            return self.assets.install_engine(gpu)
        return self.assets.install_model(key.split(":", 1)[1])

    def _live(self, key):
        return self.comps.get(key) if key in components.SOURCES else self.assets.get(key)

    def _run(self):
        job = self.job
        for it in job["items"]:
            if self._stop.is_set():
                break
            with self.lock:
                it.update(status="working", message="准备下载…", started=time.time())
            while True:
                try:
                    ok = self._install(it["key"], job["gpu"])
                except Exception as e:   # 正常不会走到这里，组件管理器自己会把错误写进状态
                    ok = False
                    with self.lock:
                        it["message"] = str(e)[:200]
                with self.lock:
                    again = not ok and self._restart and not self._stop.is_set()
                    if again:
                        self._restart = False
                        it.update(message="换加速镜像重新下载…", started=time.time(), done=0, total=0, progress=0.0)
                if not again:
                    break
            live = self._live(it["key"])
            with self.lock:
                if ok:
                    total = live.get("total") or it["total"] or it["size"]
                    it.update(status="done", progress=1.0, done=total, total=total,
                              message=live.get("message") or "已装好")
                elif self._stop.is_set():
                    it.update(status="stopped", message="已停止")
                else:
                    it.update(status="error", message=live.get("message") or it["message"] or "没装上")
        with self.lock:
            if all(it["status"] == "done" for it in job["items"]):
                job["status"] = "done"
            elif self._stop.is_set():
                job["status"] = "stopped"
            else:
                job["status"] = "error"
            job["finished"] = time.time()

    # ---------- 进度 ----------
    def status(self):
        with self.lock:
            return self._view()

    def _view(self):
        job = self.job
        if not job:
            return {"status": "idle", "items": [], "done": 0, "total": 0, "speed": 0, "eta": None,
                    "current": None, "index": 0, "count": 0, "slow": False}
        items, done, total, speed, slow = [], 0, 0, 0.0, False
        mirror = (self.settings["github_mirror"] or "").strip()
        now = time.time()
        for i, it in enumerate(job["items"]):
            view = {k: it[k] for k in ("key", "label", "size", "status", "message", "done", "total", "progress")}
            if it["status"] == "working":
                live = self._live(it["key"])
                view.update(done=live.get("done", 0), total=live.get("total", 0),
                            progress=live.get("progress", 0.0), message=live.get("message") or it["message"])
                speed = float(live.get("speed") or 0)
                elapsed = now - (it["started"] or now)
                avg = view["done"] / elapsed if elapsed > 0 else 0
                if (not mirror and _from_github(it["key"]) and elapsed > SLOW_AFTER and avg < SLOW_BPS
                        and (not view["total"] or view["done"] < view["total"])):
                    slow = True
            size = view["total"] or it["size"]
            total += size
            done += size if it["status"] == "done" else min(view["done"], size)
            items.append(view)
        cur = next((i for i, it in enumerate(job["items"]) if it["status"] == "working"), None)
        eta = (total - done) / speed if speed > 0 and total > done else None
        return {"status": job["status"], "items": items, "done": done, "total": total, "speed": speed, "eta": eta,
                "current": job["items"][cur]["key"] if cur is not None else None,
                "index": (cur + 1) if cur is not None else 0, "count": len(items), "slow": slow,
                "mirror": mirror}


def _from_github(key):
    return key in components.SOURCES or key == "engine"


def _model_keys(assets):
    return [m["key"] for m in assets.status()["models"]]
