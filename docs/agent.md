# 拾影安装说明（给 AI Agent）

你在帮用户把「拾影 ShiYing」（免费开源的 Windows 视频下载器，https://github.com/Jaqennn/ShiYing ）装到他的 Windows 电脑上。拾影是绿色软件：解压就能用，不写注册表、不装服务、不要管理员权限。下载用的组件（yt-dlp、ffmpeg、deno）由拾影自己的安装向导下载。

- 安装包：`https://github.com/Jaqennn/ShiYing/releases/latest/download/ShiYing-windows-x64.zip`（每一版都叫这个名字，这个地址永远是最新版）
- 程序：解压后的 `ShiYing\ShiYing.exe`，必须和旁边的 `_internal` 文件夹放在一起
- 默认装到：`%LOCALAPPDATA%\Programs\ShiYing`（用户指定了别的文件夹就用用户的，但不要用 `C:\Program Files`，那里没有写入权限）
- 组件和识别模型：`%LOCALAPPDATA%\ShiYing\`；设置和下载记录：`%APPDATA%\ShiYing\`
- 支持：Windows 10、Windows 11，64 位；需要 Microsoft Edge WebView2 运行时

## 规则

1. **亲自在用户电脑上运行命令**（PowerShell），不要只把步骤列给用户。如果你不能执行命令（比如你是手机版或网页版聊天），直接告诉用户：请换成能执行命令的电脑版 Agent（Claude Code、Codex、豆包电脑版、千问电脑版、Trae、WorkBuddy 等），并允许它执行命令、访问网络。
2. **不要假装成功。** 每一步都看命令的真实输出；最后必须做完「验收」，把原始输出原样贴给用户。没看到这些输出，就不能说装好了。
3. **只从 https://github.com/Jaqennn/ShiYing/releases 下载拾影**，组件只用拾影的安装向导或本文第 5 步列出的官方 GitHub 地址下载。不要从别的网站找拾影或组件，不要装其他软件（下载器、浏览器插件、「加速器」等）。
4. **不要关闭杀毒软件，不要往 Windows 安全中心里加排除项，不要改 SmartScreen 设置。** 被拦了就停下来告诉用户（见「常见情况」），由用户自己决定。
5. 只删除这次安装自己放上去的东西（下载的压缩包、解压用的临时文件夹）。不要动用户的视频和其他文件。
6. 安装 WebView2、结束正在运行的拾影之前，先征得用户同意。
7. 失败时把命令和输出原样给用户看，再对照下面的「常见情况」处理。按这份说明装不上时，停下来，不要自己想别的办法。

## 第 1 步：看系统（必须）

```powershell
[Environment]::Is64BitOperatingSystem
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuild
$guid = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
@("HKLM:\SOFTWARE\WOW6432Node\$guid", "HKLM:\SOFTWARE\$guid", "HKCU:\Software\$guid") |
  ForEach-Object { (Get-ItemProperty $_ -ErrorAction SilentlyContinue).pv }
```

- 第一行要是 `True`。`False`（32 位系统）不支持，告诉用户，不要继续。
- 第二行是系统版本号：`22000` 及以上是 Windows 11，`10240` 到 `21999` 是 Windows 10，都可以。更小（Windows 7、8.1）不支持，告诉用户，不要继续。
- 最后几行是 WebView2 的版本号，有一个像 `142.0.3595.53` 这样的数就行。全是空的或者只有 `0.0.0.0`，说明没装 WebView2：征得用户同意后安装微软官方的引导程序，再重新运行上面的命令确认：

  ```powershell
  curl.exe -fL -o "$env:TEMP\MicrosoftEdgeWebview2Setup.exe" "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
  Start-Process -Wait "$env:TEMP\MicrosoftEdgeWebview2Setup.exe" -ArgumentList '/silent', '/install'
  ```

## 第 2 步：下载并核对

```powershell
$zip = "$env:TEMP\ShiYing-windows-x64.zip"
curl.exe -fL --retry 3 -o $zip "https://github.com/Jaqennn/ShiYing/releases/latest/download/ShiYing-windows-x64.zip"
(Get-Item $zip).Length
(Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$rel = Invoke-RestMethod "https://api.github.com/repos/Jaqennn/ShiYing/releases/latest"
$rel.tag_name
($rel.assets | Where-Object name -eq 'ShiYing-windows-x64.zip').digest
```

- 大小应该在 10 MB 以上。
- 倒数第三行算出来的 SHA-256，要和最后一行 `sha256:` 后面的一样（发布页说明的末尾也写了同一个值）。不一样就删掉重新下载，还是不一样就停下来告诉用户。
- 下载很慢或者连不上 github.com：把下载地址换成 `https://ghfast.top/https://github.com/Jaqennn/ShiYing/releases/latest/download/ShiYing-windows-x64.zip` 再试（第三方加速镜像，所以一定要核对 SHA-256）。`api.github.com` 也打不开时，把算出来的 SHA-256 给用户，请他和发布页说明末尾写的对一下。

## 第 3 步：解压

先看以前有没有装过拾影、装在哪（桌面快捷方式指向的程序，和正在运行的拾影）：

```powershell
$sh = New-Object -ComObject WScript.Shell
Get-ChildItem ([Environment]::GetFolderPath('Desktop')) -Filter *.lnk |
  ForEach-Object { $sh.CreateShortcut($_.FullName).TargetPath } | Where-Object { $_ -like '*\ShiYing.exe' }
Get-Process ShiYing -ErrorAction SilentlyContinue | Select-Object Path
```

- 没有输出：第一次装，按下面的默认位置装。
- 有输出（比如 `D:\Tools\ShiYing\ShiYing.exe`）：这是升级。下面的 `$programs` 改成这个 `ShiYing` 文件夹的上一级（例子里是 `D:\Tools`），这样会覆盖旧版本，快捷方式也不用重建。组件和设置不在这个文件夹里，不会丢，也不用重装。

再看拾影是不是正开着：

```powershell
Get-Process ShiYing -ErrorAction SilentlyContinue
```

有输出就请用户先关掉拾影（正在下载的任务会中断），或者征得同意后运行 `Get-Process ShiYing | Stop-Process`。然后：

```powershell
$programs = "$env:LOCALAPPDATA\Programs"   # 升级时改成旧 ShiYing 文件夹的上一级
New-Item -ItemType Directory -Force $programs | Out-Null
Expand-Archive -Force $zip $programs
$dest = "$programs\ShiYing"
Get-ChildItem -Recurse $dest | Unblock-File
Test-Path "$dest\ShiYing.exe"
Test-Path "$dest\_internal"
Remove-Item $zip
```

- 压缩包里自带一层 `ShiYing` 文件夹，所以程序在 `$dest\ShiYing.exe`。两个 `Test-Path` 都要是 `True`。
- `Unblock-File` 去掉「从网上下载」的标记，这样打开时一般不会再弹「Windows 已保护你的电脑」。
- 升级就是覆盖解压：设置、下载记录、组件和模型都不在这个文件夹里，不会丢。

第一次装时建一个桌面快捷方式，名字是「拾影」（用字符编码写这两个字，避免命令行乱码）；升级时桌面上已经有指向 `ShiYing.exe` 的快捷方式，就跳过这一步：

```powershell
$name = [string][char]0x62FE + [char]0x5F71
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = "$dest\ShiYing.exe"
$s.WorkingDirectory = $dest
$s.Save()
Test-Path $lnk
```

## 第 4 步：打开拾影，装组件

```powershell
Start-Process "$dest\ShiYing.exe"
```

告诉用户：

- 升级的话，以前装好的组件会自动找到，都在就不会弹出向导；只缺某一个时，向导里装好的显示「已装好」，只下载缺的那个。
- 第一次打开会弹出「装好组件就能开始下载」的安装向导，点右下角的「一键安装」，等进度条走完（一共要下载约 260 MB，网速快一两分钟）。
- 进度很慢（每秒不到 200 KB）时，向导里会出现「换加速镜像」，点它就行。
- 想给英文视频生成中文字幕，可以在向导里顺便勾上「给英文视频生成中文字幕」，不需要可以不勾，以后在设置里也能装。
- 万一弹出「Windows 已保护你的电脑」：点「更多信息」→「仍要运行」。拾影没有买数字签名证书，这是正常的。

等用户说装完了，或者每隔一段时间运行一次第 6 步的检查命令，三个组件都有版本号就是装好了。

## 第 5 步（备用）：向导装不上时手动装组件

只有安装向导反复失败时才用。把官方文件下载到拾影的组件文件夹，并和 GitHub 上的官方 SHA-256 核对：

```powershell
$bin = "$env:LOCALAPPDATA\ShiYing\bin"
New-Item -ItemType Directory -Force $bin | Out-Null
$tmp = "$env:TEMP\shiying-components"
New-Item -ItemType Directory -Force $tmp | Out-Null

function Get-Official($repo, $tag, $asset, $out) {
  $api = if ($tag) { "https://api.github.com/repos/$repo/releases/tags/$tag" } else { "https://api.github.com/repos/$repo/releases/latest" }
  $a = (Invoke-RestMethod $api).assets | Where-Object name -eq $asset
  curl.exe -fL --retry 3 -o $out $a.browser_download_url
  $want = ($a.digest -replace '^sha256:', '').ToLower()
  $got = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
  "$asset  官方 $want"
  "$asset  下载 $got"
  if ($want -and $want -ne $got) { Remove-Item $out; throw "$asset 校验没通过，已删除" }
}

Get-Official 'yt-dlp/yt-dlp' $null 'yt-dlp.exe' "$bin\yt-dlp.exe"
Get-Official 'yt-dlp/FFmpeg-Builds' 'latest' 'ffmpeg-master-latest-win64-gpl.zip' "$tmp\ffmpeg.zip"
Get-Official 'denoland/deno' $null 'deno-x86_64-pc-windows-msvc.zip' "$tmp\deno.zip"

Expand-Archive -Force "$tmp\ffmpeg.zip" "$tmp\ffmpeg"
Get-ChildItem -Recurse "$tmp\ffmpeg" -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $bin
Expand-Archive -Force "$tmp\deno.zip" $bin
Remove-Item -Recurse -Force $tmp
```

- 每个文件的「官方」和「下载」两行要一样，不一样会自动删掉并报错。
- 直连 GitHub 太慢时，可以把 `curl.exe` 那行的地址前面加上 `https://ghfast.top/`（只加在 `github.com` 的下载地址前面，`api.github.com` 不用加）。
- 装完后在拾影里点右上角设置 → 组件，能看到三个组件的版本号。

## 第 6 步：验收（必须）

```powershell
$dest = "$env:LOCALAPPDATA\Programs\ShiYing"   # 装在别处（升级的情况）就换成那个 ShiYing 文件夹
$bin = "$env:LOCALAPPDATA\ShiYing\bin"
Test-Path "$dest\ShiYing.exe"
Get-Process ShiYing -ErrorAction SilentlyContinue | Select-Object Id, MainWindowTitle
& "$bin\yt-dlp.exe" --version
& "$bin\ffmpeg.exe" -version | Select-Object -First 1
& "$bin\deno.exe" --version | Select-Object -First 1
& "$bin\yt-dlp.exe" --simulate --print title "https://www.bilibili.com/video/BV1GJ411x7h7"
```

成功是这样的：

- 第一行 `True`
- 有一个 ShiYing 进程，窗口标题是「拾影」
- yt-dlp 输出一个日期样的版本号（例如 `2026.10.22`），ffmpeg 第一行以 `ffmpeg version` 开头，deno 第一行以 `deno 2.` 开头
- 最后一条打印出这个 B 站视频的标题（B 站国内不用代理）。视频要是被删了，换任意一个 B 站视频链接再试

把这些输出原样贴给用户，再请用户在拾影里试一次：在手机或浏览器里复制一个视频链接，回到拾影窗口，它会自动认出链接，选清晰度后点「下载」。

## 恢复原样 / 卸载

征得用户同意后：

```powershell
Get-Process ShiYing -ErrorAction SilentlyContinue | Stop-Process
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\ShiYing"   # 程序（装在别处就换成那个 ShiYing 文件夹）
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\ShiYing"            # 组件和识别模型，最占空间
Remove-Item -Recurse -Force "$env:APPDATA\ShiYing"                 # 设置和下载记录
$name = [string][char]0x62FE + [char]0x5F71
Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk")
```

下载好的视频在用户设置的保存位置（默认「视频\拾影」），卸载不会删，也不要替用户删。

## 常见情况

| 现象 | 怎么办 |
|---|---|
| `curl: (28)`、`Failed to connect`、很久不动 | GitHub 直连慢：用第 2 步说的 `https://ghfast.top/` 加速地址，下完一定核对 SHA-256；或者请用户打开代理软件的「系统代理」再试 |
| `Expand-Archive` 报错说文件损坏 | 下载不完整：删掉压缩包重新下载，核对大小和 SHA-256 |
| 解压后 `ShiYing.exe` 不见了，或者被杀毒软件提示删除 | PyInstaller 打包的程序常被误报。SHA-256 和官方一致就是原版。告诉用户可以在杀毒软件里把拾影文件夹加入信任，要不要加由用户决定，不要自己关杀毒、加排除项 |
| 双击没反应、窗口一闪就没了 | 先回到第 1 步确认 WebView2 有版本号；再确认 `ShiYing.exe` 和 `_internal` 在同一个文件夹，不在 `C:\Program Files` 里 |
| 安装向导一直停在「连接 GitHub…」或提示连不上 | 点向导里的「换加速镜像」；还不行请用户打开代理的「系统代理」后点「重试没装上的」；再不行用第 5 步手动装 |
| 安装向导提示「文件校验没通过」 | 下载的文件和官方不一致，拾影已经删掉了：点「重试没装上的」重新下载；换了镜像还这样，就关掉镜像直连 GitHub |
| 验收时 yt-dlp 能出版本号，但 B 站标题打印失败 | 先换一个 B 站视频链接；还不行把完整输出给用户，请他点拾影右上角「反馈」发给开发者 |
