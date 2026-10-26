"""任务调度：解析缓存、下载队列、并发控制、历史记录。"""
import json
import threading
import time
import uuid
from pathlib import Path

from . import components, detect, douyin, ytdlp
from .netutil import resolve_proxy
from .paths import DATA_DIR

HISTORY_FILE = DATA_DIR / "history.json"
ACTIVE = ("queued", "downloading", "merging")
_PUBLIC = ("id", "url", "platform", "site", "title", "uploader", "thumbnail", "duration", "quality",
           "quality_label", "status", "progress", "speed", "eta", "downloaded", "total",
           "filepath", "error", "created", "finished", "count",
           "sub_status", "sub_progress", "sub_msg", "sub_error", "sub_files")


class TaskManager:
    def __init__(self, settings):
        self.settings = settings
        self.on_done = None       # 下载完成回调（自动生成字幕用）
        self.tasks = {}
        self.order = []
        self.probes = {}          # probe_id -> 解析结果（含内部候选地址）
        self.cancels = {}
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self._load_history()
        threading.Thread(target=self._scheduler, daemon=True).start()

    # ---------- 解析 ----------
    def parse(self, text: str) -> dict:
        hit = detect.extract(text)
        if not hit:
            raise ValueError("没找到链接，粘贴视频的播放链接或分享文本试试")
        url, plat = hit["url"], hit["platform"]
        s = self.settings
        if plat == "douyin":
            try:
                info = douyin.parse(url, cookie=s["douyin_cookie"], proxy=resolve_proxy(s, "douyin"))
            except douyin.DouyinError as e:
                # 兜底：配置了抖音 cookies.txt 时交给 yt-dlp 再试一次
                if s["cookies"]["douyin"]["mode"] != "none" and components.find("yt-dlp"):
                    info = ytdlp.probe(url, plat, s)
                else:
                    raise ValueError(str(e))
        else:
            info = ytdlp.probe(url, plat, s)
        pid = uuid.uuid4().hex[:12]
        info["platform"] = plat
        with self.lock:
            self.probes[pid] = info
            if len(self.probes) > 50:
                self.probes.pop(next(iter(self.probes)))
        public = {k: v for k, v in info.items() if not k.startswith("_")}
        public["probe_id"] = pid
        public["platform_name"] = (info.get("site") if plat == "other" else None) or detect.PLATFORM_META[plat]["name"]
        public["default_quality"] = self._pick_default(info["qualities"])
        return public

    def _pick_default(self, qualities):
        want = str(self.settings["default_quality"])
        keys = [q["key"] for q in qualities]
        if want in keys:
            return want
        if want.isdigit():
            # 想要 1080 但只有 720 → 选不超过目标的最高档
            nums = sorted((int(k) for k in keys if k.isdigit()), reverse=True)
            for n in nums:
                if n <= int(want):
                    return str(n)
            if nums:
                return str(nums[-1])
        return keys[0]

    # ---------- 队列 ----------
    def add(self, probe_id: str, quality: str, auto_sub: bool = False) -> dict:
        with self.lock:
            info = self.probes.get(probe_id)
            if not info:
                raise ValueError("解析结果已过期，请重新解析")
            q = next((x for x in info["qualities"] if x["key"] == quality), info["qualities"][0])
            tid = uuid.uuid4().hex[:10]
            task = {
                "id": tid, "url": info["url"], "platform": info["platform"],
                "site": info.get("site", ""),
                "title": info["title"], "uploader": info.get("uploader", ""),
                "thumbnail": info.get("thumbnail", ""), "duration": info.get("duration", 0),
                "quality": q["key"], "quality_label": (q["label"] + (" " + q["sub"] if q.get("sub") else "")),
                "status": "queued", "progress": 0.0, "speed": 0, "eta": None,
                "downloaded": 0, "total": q.get("size") or 0, "filepath": "", "error": "",
                "created": time.time(), "finished": None, "count": info.get("count", 1),
                "_probe": info,
                "_auto_sub": bool(auto_sub) and not q["key"].startswith("audio") and info.get("kind") != "images",
            }
            self.tasks[tid] = task
            self.order.insert(0, tid)
        self._wake.set()
        return self._public(task)

    def add_local(self, path: str) -> dict:
        """本地视频（只用来做字幕）。"""
        p = Path(path)
        if not p.is_file():
            raise ValueError("文件不存在")
        with self.lock:
            for t in self.tasks.values():
                if t.get("platform") == "local" and t.get("filepath") == str(p):
                    return self._public(t)
            tid = uuid.uuid4().hex[:10]
            now = time.time()
            task = {"id": tid, "url": "", "platform": "local", "title": p.stem, "uploader": "",
                    "thumbnail": "", "duration": 0, "quality": "local", "quality_label": "本地文件",
                    "status": "done", "progress": 1.0, "speed": 0, "eta": None, "downloaded": 0,
                    "total": p.stat().st_size, "filepath": str(p), "error": "", "created": now,
                    "finished": now, "count": 1}
            self.tasks[tid] = task
            self.order.insert(0, tid)
        self._save_history()
        return self._public(task)

    def save(self):
        self._save_history()

    def list(self):
        with self.lock:
            return [self._public(self.tasks[t]) for t in self.order if t in self.tasks]

    def cancel(self, tid):
        with self.lock:
            t = self.tasks.get(tid)
            if not t or t["status"] not in ACTIVE:
                return False
            t["_canceled"] = True
            if t["status"] == "queued":
                t["status"] = "canceled"
            ev = self.cancels.get(tid)
        if ev:
            ev.set()
        return True

    def retry(self, tid):
        with self.lock:
            t = self.tasks.get(tid)
            if not t or t["status"] in ACTIVE:
                return False
            if "_probe" not in t:  # 历史记录里的任务：重新解析
                raise ValueError("NEED_REPARSE")
            t.update(status="queued", progress=0.0, speed=0, eta=None, error="", finished=None)
            t.pop("_canceled", None)
        self._wake.set()
        return True

    def remove(self, tid):
        self.cancel(tid)
        with self.lock:
            self.tasks.pop(tid, None)
            if tid in self.order:
                self.order.remove(tid)
        self._save_history()
        return True

    def clear_finished(self):
        with self.lock:
            for tid in list(self.order):
                if self.tasks[tid]["status"] not in ACTIVE:
                    self.tasks.pop(tid)
                    self.order.remove(tid)
        self._save_history()

    def get(self, tid):
        with self.lock:
            return self.tasks.get(tid)

    # ---------- 调度 ----------
    def _scheduler(self):
        while True:
            self._wake.wait(1.0)
            self._wake.clear()
            with self.lock:
                running = sum(1 for t in self.tasks.values() if t["status"] in ("downloading", "merging"))
                limit = int(self.settings["max_concurrent"])
                # 先加入的先下载
                queued = [self.tasks[t] for t in reversed(self.order)
                          if t in self.tasks and self.tasks[t]["status"] == "queued"]
                for t in queued[: max(0, limit - running)]:
                    t["status"] = "downloading"
                    ev = threading.Event()
                    self.cancels[t["id"]] = ev
                    threading.Thread(target=self._run, args=(t, ev), daemon=True).start()

    def _out_dir(self, platform):
        base = Path(self.settings["download_dir"]).expanduser()
        if self.settings["split_by_platform"]:
            base = base / detect.PLATFORM_META[platform]["folder"]
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _run(self, task, ev):
        def upd(d):
            with self.lock:
                if task["status"] in ("canceled",):
                    return
                task.update(d)
        try:
            out = self._out_dir(task["platform"])
            info = task["_probe"]
            if info.get("engine") == "douyin":
                path = douyin.download(info, task["quality"], out, upd, ev,
                                       proxy=resolve_proxy(self.settings, "douyin"),
                                       ffmpeg=components.find("ffmpeg"),
                                       connections=int(self.settings["connections"]) if self.settings["accelerate"] else 1)
            else:
                path = ytdlp.download(task, self.settings, out, upd, ev)
            with self.lock:
                task.update(status="done", progress=1.0, speed=0, eta=None,
                            filepath=path or str(out), finished=time.time())
            if task.get("_auto_sub") and self.on_done:
                try:
                    self.on_done(task["id"])
                except Exception:
                    pass
        except Exception as e:
            with self.lock:
                if task.get("_canceled"):
                    task.update(status="canceled", speed=0, eta=None, error="")
                else:
                    task.update(status="error", speed=0, eta=None, error=str(e) or e.__class__.__name__)
                task["finished"] = time.time()
        finally:
            self.cancels.pop(task["id"], None)
            self._save_history()
            self._wake.set()

    # ---------- 历史 ----------
    def _public(self, t):
        return {k: t.get(k) for k in _PUBLIC}

    def _save_history(self):
        with self.lock:
            done = [self._public(self.tasks[t]) for t in self.order
                    if t in self.tasks and self.tasks[t]["status"] in ("done", "error", "canceled")][:300]
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = HISTORY_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(done, ensure_ascii=False), "utf-8")
            tmp.replace(HISTORY_FILE)
        except Exception:
            pass

    def _load_history(self):
        try:
            items = json.loads(HISTORY_FILE.read_text("utf-8"))
        except Exception:
            return
        for it in items:
            if it.get("status") in ACTIVE:
                it["status"] = "error"
                it["error"] = "程序关闭时未完成"
            if it.get("sub_status") not in (None, "", "done", "error"):
                it["sub_status"], it["sub_msg"] = "", ""   # 关闭程序时没做完的字幕
            self.tasks[it["id"]] = it
            self.order.append(it["id"])
