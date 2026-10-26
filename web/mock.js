/* 在普通浏览器里直接打开 index.html 时使用的演示后端（不会在软件里生效） */
(() => {
  const thumb = (a, b) => "data:image/svg+xml," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient></defs><rect width="160" height="90" fill="url(#g)"/><circle cx="118" cy="30" r="14" fill="#fff" opacity=".35"/><path d="M0 90 L52 46 L84 70 L112 52 L160 86 V90Z" fill="#000" opacity=".22"/></svg>`);
  const now = Date.now() / 1000;
  const ok = (data) => Promise.resolve({ ok: true, data });
  const settings = {
    download_dir: "D:\\Videos\\拾影", split_by_platform: true, filename_template: "%(title).60s [%(id)s].%(ext)s",
    compat_mode: true, container: "mp4", default_quality: "1080", max_concurrent: 2, accelerate: true, connections: 16, fragments: 8,
    proxy_mode: "system", proxy_url: "http://127.0.0.1:7890", _system_proxy: "http://127.0.0.1:7890",
    proxy_platforms: { youtube: true, x: true, bilibili: false, douyin: false, other: true, translate: false },
    cookies: { youtube: { mode: "none", file: "", browser: "firefox" }, bilibili: { mode: "file", file: "D:\\cookies\\bilibili.txt", browser: "firefox" },
      x: { mode: "browser", file: "", browser: "firefox" }, douyin: { mode: "none", file: "", browser: "firefox" }, other: { mode: "none", file: "", browser: "firefox" } },
    douyin_cookie: "", github_mirror: "", watch_clipboard: true, theme: "system",
    sub_model: "large-v3-turbo", sub_vad: true, sub_translator: "deepl", deepl_key: "", llm_base_url: "https://api.deepseek.com",
    llm_key: "", llm_model: "deepseek-chat", sub_outputs: { zh: true, bilingual: true, en: false }, sub_auto: true, sub_prefer_platform: true,
  };
  const tasks = [
    { id: "a1", platform: "youtube", title: "一台老式胶片相机的完整拆解与清洁保养", thumbnail: thumb("#2f4858", "#86bbd8"),
      quality: "1080", quality_label: "1080P 60帧", status: "downloading", progress: 0.42, speed: 6.3e6, eta: 38, downloaded: 2.1e8, total: 5.0e8, created: now - 60 },
    { id: "a2", platform: "bilibili", title: "【手工】用一整块木头做一把勺子，全程无剪辑", thumbnail: thumb("#6b4f3a", "#e3b778"),
      quality_label: "1080P", status: "merging", progress: 1, speed: 0, downloaded: 3.2e8, total: 3.2e8, created: now - 120 },
    { id: "a3", platform: "douyin", title: "清晨的海边，浪花一层一层退下去", thumbnail: thumb("#1b4965", "#bee9e8"),
      quality_label: "1080P 最高", status: "queued", progress: 0, speed: 0, created: now - 130 },
    { id: "a4", platform: "x", title: "Time-lapse of a city skyline from dusk to midnight", thumbnail: thumb("#22223b", "#9a8c98"),
      quality_label: "4K", status: "done", progress: 1, total: 3.9e9, finished: now - 3600, created: now - 3700, filepath: "x.mp4",
      sub_status: "recognizing", sub_progress: 0.45, sub_msg: "识别语音 45%" },
    { id: "a7", platform: "youtube", title: "How a mechanical watch actually keeps time", thumbnail: thumb("#344e41", "#a3b18a"),
      quality_label: "1080P 60帧", status: "done", progress: 1, total: 6.4e8, finished: now - 5400, created: now - 5500, filepath: "w.mp4",
      sub_status: "done", sub_progress: 1, sub_msg: "用 YouTube 原字幕翻译（312 条）", sub_files: ["w.zh.srt", "w.zh-en.srt"] },
    { id: "a8", platform: "local", title: "讲座录像 第三讲", thumbnail: "", quality: "local",
      quality_label: "本地文件", status: "done", progress: 1, total: 1.2e9, finished: now - 6000, created: now - 6000, filepath: "l.mp4",
      sub_status: "error", sub_error: "DeepL 本月额度用完了（免费版每月 50 万字符），换个密钥或下月再试" },
    { id: "a5", platform: "youtube", title: "城市夜跑路线推荐：沿江 10 公里", thumbnail: thumb("#3d405b", "#e07a5f"),
      quality_label: "4K", status: "error", progress: 0.1, error: "YouTube 要求验证不是机器人：换个代理节点，或在设置里给 YouTube 导入 Cookie", created: now - 7200 },
    { id: "a6", platform: "bilibili", title: "家常红烧肉，一次讲清楚火候", thumbnail: thumb("#7f5539", "#ddb892"),
      quality_label: "音频 M4A", status: "done", progress: 1, total: 6.1e6, finished: now - 90000, created: now - 90100, filepath: "a.m4a" },
  ];
  setInterval(() => {
    const t = tasks[0];
    if (t.status !== "downloading") return;
    t.progress = Math.min(1, t.progress + 0.01);
    t.downloaded = t.total * t.progress;
    t.eta = Math.max(0, Math.round((1 - t.progress) * 65));
    if (t.progress >= 1) t.status = "merging";
  }, 500);

  const probe = {
    probe_id: "p1", engine: "ytdlp", platform: "youtube", platform_name: "YouTube", url: "https://youtu.be/demo",
    id: "demo", title: "从零开始搭一台家用 NAS：选盘、装系统到远程访问一次讲完", uploader: "木桌实验室", duration: 1834,
    thumbnail: thumb("#264653", "#2a9d8f"), count: 1, hint: "",
    qualities: [
      { key: "2160", label: "4K", sub: "60帧", size: 2.4e9 }, { key: "1440", label: "2K", sub: "60帧", size: 1.3e9 },
      { key: "1080", label: "1080P", sub: "60帧", size: 6.1e8 }, { key: "720", label: "720P", sub: "", size: 2.9e8 },
      { key: "480", label: "480P", sub: "", size: 1.2e8 }, { key: "audio_m4a", label: "音频", sub: "M4A", size: 2.9e7 },
      { key: "audio_mp3", label: "音频", sub: "MP3", size: 0 },
    ],
    default_quality: "1080",
  };

  window.ShiYingMock = {
    app_info: () => ok({ name: "拾影", version: "0.1.0 (演示)", data_dir: "C:\\Users\\you\\AppData\\Roaming\\ShiYing", missing: ["deno"] }),
    detect: (t) => ok(/youtu/.test(t) ? { platform: "youtube", url: t } : /douyin/.test(t) ? { platform: "douyin", url: t } : /b23|bilibili/.test(t) ? { platform: "bilibili", url: t } : { platform: "other", url: t }),
    clipboard: () => ok(""),
    detect_all: (t) => ok((t.match(/https?:\/\/\S+/g) || []).map((u) => ({ url: u, platform: /youtu/.test(u) ? "youtube" : /douyin/.test(u) ? "douyin" : /b23|bilibili/.test(u) ? "bilibili" : /x\.com/.test(u) ? "x" : "other" }))),
    parse: (t) => new Promise((r) => {
      const plat = /douyin/.test(t) ? "douyin" : /b23|bilibili/.test(t) ? "bilibili" : /x\.com/.test(t) ? "x" : "youtube";
      if (/fail/.test(t)) return setTimeout(() => r({ ok: false, error: "这条推文里没有视频" }), 900);
      const variants = {
        youtube: probe,
        bilibili: { ...probe, probe_id: "p2", platform: "bilibili", title: "【手工】用一整块木头做一把勺子，全程无剪辑", uploader: "木作日常", duration: 742, thumbnail: thumb("#6b4f3a", "#e3b778"),
          hint: "未登录最高 1080P。1080P 高码率、4K 需要在设置里导入 B站 Cookie（4K 需大会员）",
          qualities: [{ key: "1080", label: "1080P", sub: "", size: 2.1e8 }, { key: "720", label: "720P", sub: "", size: 1.1e8 }, { key: "audio_m4a", label: "音频", sub: "M4A", size: 1.2e7 }] },
        douyin: { ...probe, probe_id: "p3", platform: "douyin", title: "清晨的海边，浪花一层一层退下去", uploader: "海边的小林", duration: 24, thumbnail: thumb("#1b4965", "#bee9e8"), hint: "",
          qualities: [{ key: "1080p", label: "1080P", sub: "最高", size: 0 }, { key: "720p", label: "720P", sub: "", size: 0 }, { key: "audio_m4a", label: "音频", sub: "M4A", size: 0 }] },
        x: { ...probe, probe_id: "p4", platform: "x", title: "Time-lapse of a city skyline from dusk to midnight", uploader: "@skyline_daily", duration: 1140, thumbnail: thumb("#22223b", "#9a8c98"), hint: "",
          qualities: [{ key: "2160", label: "4K", sub: "", size: 3.9e9 }, { key: "1080", label: "1080P", sub: "", size: 9.8e8 }, { key: "720", label: "720P", sub: "", size: 4.1e8 }, { key: "audio_m4a", label: "音频", sub: "M4A", size: 2e7 }] },
      };
      setTimeout(() => r({ ok: true, data: variants[plat] }), 400 + Math.random() * 1200);
    }),
    add_task: () => ok({}),
    tasks: () => ok(tasks.map((t) => ({ ...t }))),
    cancel: () => ok(true), retry: () => ok(true), remove: () => ok(true), clear_finished: () => ok(true),
    open_task: () => ok(true), reveal_task: () => ok(true), open_download_dir: () => ok(true), open_bin_dir: () => ok(true),
    get_settings: () => ok(JSON.parse(JSON.stringify(settings))),
    save_settings: (p) => ok(JSON.parse(JSON.stringify(Object.assign(settings, p)))),
    choose_folder: () => ok(null), choose_cookie_file: () => ok(null),
    components: () => ok([
      { name: "yt-dlp", desc: "下载引擎（YouTube / B站 / X）", version: "2026.10.12", local: true, status: "idle" },
      { name: "ffmpeg", desc: "合并音视频、转音频", version: "N-121345-g8a1b", local: true, status: "idle" },
      { name: "deno", desc: "YouTube 解签名所需的 JS 运行时", version: null, local: false, status: "working", progress: 0.37, message: "15.2 / 41.0 MB" },
    ]),
    install_component: () => ok(true),
    subtitle: () => ok(true), cancel_subtitle: () => ok(true), reveal_subtitle: () => ok(true), open_models_dir: () => ok(true),
    add_local_videos: () => ok([]), install_whisper: () => ok(true), install_model: () => ok(true),
    check_translator: () => new Promise((r) => setTimeout(() => r({ ok: true, data: "DeepL 可用：本月已用 18,422 / 500,000 字符" }), 500)),
    sub_assets: () => ok({
      engine: { installed: true, build: "whisper-cublas-12.4.0-bin-x64.zip v1.9.5", gpu: true, status: "idle" },
      nvidia: true,
      vad: { installed: true },
      models: [
        { key: "base.en", label: "快速", desc: "英文专用，速度最快，准确度一般", size: 148e6, installed: false, status: "idle" },
        { key: "small.en", label: "均衡", desc: "英文专用，速度和准确度兼顾", size: 488e6, installed: false, status: "working", progress: 0.62, message: "302 / 488 MB" },
        { key: "large-v3-turbo", label: "精准", desc: "最准，支持多种语言；没有 NVIDIA 显卡时较慢", size: 574e6, installed: true, status: "idle" },
      ],
    }),
  };
})();
