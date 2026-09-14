# livecap · 日文直播实时双语字幕

给日文直播加**实时字幕**：日文原文 + 中文翻译，浮在屏幕底部。
**只抓电脑正在播放的声音**（WASAPI 环回），所以不需要链接、不需要 cookie、不需要登录 —— 你在浏览器里正常看直播就行。

![字幕条效果：黑色半透明底、中文在上、日文在下](testdata/overlay_preview_2.png)

实测端到端延迟 **0.5 ~ 1.6 秒**（RTX 4000，本地模型，离线可跑）。

```
系统声音（你正在放的直播）
        │  WASAPI 环回采集 16kHz 单声道
        ▼
能量 VAD 按停顿切句（自适应噪声门限 + 迟滞 + 段内 AGC）
        ▼
faster-whisper large-v3（GPU float16）──► 日文
        ▼
NLLB-200（CTranslate2，GPU float16）──► 简体中文
        ▼
置顶悬浮字幕条：中文大字 + 日文小字
```

## 快速开始

1. 双击 **`dist\livecap\livecap.exe`**
2. 面板里确认「输出设备」是你现在放声音的设备（默认已选系统默认扬声器）
3. 点 **开始字幕**，面板会收进托盘，字幕条出现在屏幕底部
4. 在浏览器里播放日文直播 —— 字幕会自动跟着走

退出：右键托盘图标 → 退出（或字幕条上按 `Ctrl+Q`）。
设置面板：右键托盘图标 → 显示设置面板。

### 字幕条

屏幕底部一条**固定宽度**的横条（默认屏幕宽度的 **60%**，居中），**黑色半透明底 + 白字**（就是直播字幕那种观感），
里面**上下两行**：中文在上（粗体大字），日文在下（略小，约中文的 85%）。
每个语种各占一行、不折行 —— 句子太长时自动缩字号，条的高度始终不变。

> 底色是在 `paintEvent()` 里画的。Qt 对「无边框 + 置顶 + 半透明」的窗口**不会可靠地绘制样式表背景**，
> 早期版本因此看起来像"没有底框"，改法见 `livecap/overlay.py` 的 `BACKGROUND_COLOR` / `paintEvent`。

识别耗时、端到端延迟这些状态信息不再占用字幕条，改看**托盘图标悬停提示**和 `livecap.log`。

| 快捷键 | 作用 |
| --- | --- |
| 鼠标左键拖动 | 移动位置（**位置会被记住**，下一句不会跳回原处，重启也还在） |
| `Ctrl+Shift+H` | 显示 / 隐藏 |
| `Ctrl+Shift+T` | 鼠标穿透（点了不挡浏览器） |
| `Ctrl+Shift+O` / `Ctrl+Shift+I` | 字号变大 / 变小 |
| `Ctrl+Shift+C` | 清空当前字幕 |
| `Ctrl+Shift+R` | 字幕条回到默认位置（底部居中） |
| `Ctrl+Q` | 退出 |

命令行版可以调：`--bar-width 0.9`（更宽）、`--shrink-wrap`（改成贴合内容的小胶囊）。

### 大模型翻译的 API 设置

面板里把「中文翻译」选成**大模型 API** 时，下面会展开设置区（默认就是 DeepSeek 官方推荐配置）：

| 字段 | 说明 |
| --- | --- |
| 服务商 | `DeepSeek · deepseek-flash（推荐）` / `DeepSeek · deepseek-v4-pro` / `OpenAI` / 自定义 |
| 接口地址 | 默认 `https://api.deepseek.com`（`/v1` 也兼容，404 时会自动换另一种写法重试） |
| 模型 | 默认 **`deepseek-flash`**；下拉里还有 `deepseek-v4-pro`、`gpt-4o-mini`、`qwen3:4b`（本地 Ollama），也可以手填 |
| 思考模式 | 默认**关闭** —— DeepSeek 服务端默认是开着的，开着每条字幕要多等好几秒 |
| API Key | `sk-...`，输入框隐藏显示 |
| 测试连接 | 真发一条"把こんにちは翻成中文"的请求，成功会显示译文 + 实际用的地址和模型，失败显示 HTTP 状态和原因 |

按 [DeepSeek 官方文档](https://api-docs.deepseek.com/api/create-chat-completion) 实现的细节：

* 模型名只有 **`deepseek-flash`** 和 **`deepseek-v4-pro`** 两个；老的 `deepseek-chat` / `deepseek-reasoner`
  已经不在支持列表里了，程序会自动把它们换成 `deepseek-flash`（旧配置文件也会自动迁移）。
* 请求体带 `thinking: {"type": "disabled"}`（仅对 DeepSeek 端点发这个字段，其它兼容接口不受影响）。
* 回答取 `choices[0].message.content`；如果模型只返回了 `reasoning_content`，会明确报错而不是静默出空字幕。
* `temperature` 默认 `1.3`（DeepSeek 对翻译任务的推荐值），可用 `--llm-temperature` 调整。
* 非 200 响应会带上 HTTP 状态码和原始报文，方便直接看出是 Key 错、余额不足还是限流。

配置存在 exe 同目录的 `settings.json`（明文，本地自用；目录不可写时退回 `%APPDATA%\livecap`），
下次打开自动填好。也可以继续用环境变量 `DEEPSEEK_API_KEY`（GUI 里的 Key 优先）。

### 面板里的三个选项

| 选项 | 说明 |
| --- | --- |
| 输出设备 | 抓哪个输出设备的声音（耳机/扬声器/虚拟声卡）。换了设备点「刷新设备」 |
| 识别模型 | `large-v3` 最准；`large-v3-turbo` 更快、显存更省 |
| 中文翻译 | 本地 NLLB（离线，默认）／大模型 API（质量最好，需 `DEEPSEEK_API_KEY`）／只看日文 |

## 实测数据（本机 Quadro RTX 4000 8G）

| 项目 | 结果 |
| --- | --- |
| 日文识别 | 很准：长句、数字（`気温は18度`、`2500万人`）、专有名词（`総務省`）基本全对 |
| 识别耗时 | 0.4 ~ 1.3 秒/句 |
| 翻译耗时 | 0.08 ~ 0.27 秒/句（NLLB fp16 + GPU） |
| **端到端延迟** | **约 0.5 ~ 1.6 秒** |
| 翻译质量 | 能看懂大意，但口语句会出错，见下文 |

## 翻译质量（唯一的短板）

本地 NLLB-600M 的实测错例：

| 日文 | 输出 | 应该是 |
| --- | --- | --- |
| みなさん、こんばんは | 您好,您好. | 大家晚上好 |
| 二千五百万人を超えました | 超过2.5亿的外国游客 | 超过 2500 万人次 |
| とても勉強になりました | 我很有学习力 | 真是受教了 |
| 今日の配信はここまでにします | 现在我们要把它送到这里 | 今天的直播就到这里 |

想要明显更好的中文，两个办法：

**A. 换大模型 API（质量最好，一句话几厘钱）**
```bat
set DEEPSEEK_API_KEY=sk-xxxx
livecap-cli.exe            rem GUI 里选「大模型 API」也可以
```
GUI 选「大模型 API（质量最好，需 key）」前请先把 `DEEPSEEK_API_KEY` 设成系统环境变量。
任何 OpenAI 兼容端点都支持：`--llm-base-url` / `--llm-model` / `--llm-api-key-env`。

**B. 本地大模型（不花钱，显存紧）**
装 Ollama 拉一个 Qwen3-4B，然后
```bat
livecap-cli.exe --translator llm --llm-base-url http://127.0.0.1:11434/v1 --llm-model qwen3:4b
```
8G 显存同时装 whisper large-v3 + 4B 模型比较紧，建议配 `--asr-model deepdml/faster-whisper-large-v3-turbo-ct2`。

## 高级用法：`livecap-cli.exe`

同一个引擎的命令行版本，能直接给链接（不用手动开浏览器，也能拿到更纯净的音轨）：

```bat
rem 直接拉直播流（YouTube 现在需要 cookie）
livecap-cli.exe --source url --url "https://www.youtube.com/watch?v=xxxx" --cookies cookies.txt

rem 本地视频，按原速播放
livecap-cli.exe --source file --file "D:\video\test.mp4"

rem 抓系统声音（和 GUI 一样）
livecap-cli.exe

rem 只打印到控制台，顺便存双语字幕
livecap-cli.exe --no-overlay --log subs.jsonl
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--loopback-device "Realtek"` | 按名字挑输出设备（`livecap-cli.exe --help` 里有全部参数） |
| `--max-duration 60` | 跑 60 秒自动收尾退出 |
| `--asr-model` | 默认 `Systran/faster-whisper-large-v3` |
| `--device cpu --compute-type int8` | 没显卡时的退路（会慢很多） |
| `--vad-threshold 0.005` | 声音小、一直不出字幕时调小 |
| `--max-segment 4 --min-silence 0.25` | 想更快、句子更碎 |
| `--max-segment 10 --min-silence 0.5` | 想更准、字幕晚一点 |
| `-v` | 打印详细日志 |

> YouTube 直连会报 `Sign in to confirm you're not a bot`（这台机器还走了系统代理，IP 被标记）。
> 用扩展导出 `cookies.txt` 后加 `--cookies cookies.txt`；或者干脆用 GUI 的抓声音模式，完全不用管这个。

## 安装包（便于传播）

**现成安装包直接下载 → [Releases 页面](https://github.com/xmr876/livecap/releases)**（`livecap-setup-1.0.0.exe`，约 1.1GB）

> 安装包体积超过 GitHub 仓库单文件上限（100MB），所以放在 Releases 里当附件（附件上限 2GB）；
> Git LFS 免费额度只有 1GB/月，也放不下。想要绿色版（模型内置、拷走即用）就用下面的脚本自己构建。

自己构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
```

产出两样东西：

| 产物 | 用途 | 大小 |
| --- | --- | --- |
| `dist\livecap\` | 绿色版：整个文件夹拷走就能用（模型已在里面） | ~3.9 GB |
| `dist\installer\livecap-setup-1.0.0.exe` | **安装包**：只有一个 exe，方便传给别人 | ~1.4 GB |

安装包**不含模型**（4GB 太占地方），装完后会：

1. 装到 `%LOCALAPPDATA%\Programs\livecap` —— **按用户安装，不弹 UAC**
2. 建开始菜单项（程序 / 使用说明 / 下载模型 / 卸载）和可选的桌面快捷方式
3. 默认勾选「安装后下载模型」，用一个控制台窗口下载约 4GB：
   * 语音识别：`Systran/faster-whisper-large-v3`（~2.9GB）
   * 中文翻译：`JustFrederik/nllb-200-distilled-600M-ct2-float16`（~1.2GB，HF 上公开的 CTranslate2 版本）
   直连 HuggingFace 失败会自动改走 `hf-mirror.com` 重试；中断了从开始菜单重跑即可，已下载部分保留
4. 面板里也随时能点「检查 / 下载模型」看进度或补下载
   （命令行：`livecap-cli.exe --check-models` / `--download-models`）
5. 卸载时模型和设置一起删掉

`packaging\installer.iss` 是 Inno Setup 脚本；编译器用 `winget install JRSoftware.InnoSetup` 装，
中文界面用的是社区语言包（已放到 Inno Setup 的 `Languages` 目录）。

## 目录结构

```
livecap/
├─ dist\livecap\              ★ 打包好的成品
│  ├─ livecap.exe             双击这个（GUI，抓系统声音）
│  ├─ livecap-cli.exe         命令行版
│  ├─ models\                 翻译模型（NLLB fp16，CTranslate2 格式，1.2GB）
│  ├─ tools\ffmpeg\           静态 ffmpeg（命令行版拉流用）
│  ├─ livecap.log             运行日志（出问题先看它）
│  └─ _internal\              PyInstaller 运行时（含 CUDA 的 cuDNN/cuBLAS）
├─ livecap\                   源码
│  ├─ config.py  audio.py  segmenter.py  asr.py  translate.py
│  ├─ overlay.py               悬浮字幕条
│  ├─ gui.py                   启动面板 + 托盘
│  ├─ models.py                模型在线下载（安装包用）
│  ├─ settings.py              设置持久化（settings.json）
│  ├─ paths.py                 模型/资源定位（打包后也适用）
│  └─ main.py                  管线编排
├─ scripts\                   预取模型、转换翻译模型、自检脚本
├─ testdata\                  测试素材（日文语音 + 字幕条预览图）
├─ packaging\
│  ├─ livecap.spec             PyInstaller 配置
│  ├─ build.ps1                一键打包（两个 exe + 安装包）
│  ├─ installer.iss            Inno Setup 安装包脚本
│  └─ 使用说明.txt              随安装包装给最终用户的说明
└─ setup.ps1                  从零安装开发环境
```

语音识别模型（whisper large-v3，约 3GB）**没有**打进包里，用的是本机 HuggingFace 缓存
（`%USERPROFILE%\.cache\huggingface\hub`）。换机器时要么把那个目录拷过去，要么先在目标机跑一次
`.venv\Scripts\python.exe scripts\prefetch_models.py`。

## 重新打包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
```

会依次：生成图标 → 装 pyinstaller → 按 `packaging\livecap.spec` 构建两个 exe → 把 `models\`
和 `tools\ffmpeg` 拷到 `dist\livecap\`。产物约 3.9GB，其中 2GB 是 GPU 推理必须的 cuDNN/cuBLAS。

> 打包注意：PyInstaller 是按 PATH 找 DLL 的。本机 PATH 上有 anaconda，它自带的
> `libssl/libcrypto` 会顶掉 Python 自己的版本，导致冻结后 `import _ssl` 失败 —— spec 里已强制
> 使用 `sys.base_prefix\DLLs` 下的那份。

## 第三方组件与许可

本项目自己的代码用 MIT（见 `LICENSE`）。它依赖/调用的东西各有各的许可，**分发前请留意**：

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2) | 语音识别 / 推理引擎 | MIT |
| Whisper large-v3 权重 | 日文语音识别 | MIT（OpenAI） |
| **NLLB-200-distilled-600M 权重** | 日文中译 | **CC-BY-NC-4.0，禁止商用** |
| ffmpeg（gyan.dev 静态构建） | 取音/解码 | GPL v3（该构建） |
| PySide6 / Qt | 界面 | LGPL v3 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 拉直播流（可选） | Unlicense |
| soundcard | WASAPI 环回采集 | BSD-3 |

也就是：**自己看、自己改没问题；要商用得先把 NLLB 换成可商用的翻译模型**（比如走大模型 API，
或在面板里把「中文翻译」换成别的后端）。

## 排错

| 现象 | 处理 |
| --- | --- |
| 一直没字幕 | 直播确实在出声吗？把 `--vad-threshold` 调小到 `0.005`；看 `livecap.log` |
| 字幕慢半拍 | 换 `--asr-model deepdml/faster-whisper-large-v3-turbo-ct2`，或 `--max-segment 4` |
| 说显存不够 / 报 CUDA 错 | 会自动退回 CPU int8（慢）；也可 GUI 里换 turbo 模型 |
| 面板里没有设备 | 先播放点声音让设备激活，再点「刷新设备」；耳机换了要在 Windows 里设为默认输出 |
| 启动就报错 | 看 `dist\livecap\livecap_crash.log` 和 `livecap.log` |
| 想确认 GPU 能用 | `.venv\Scripts\python.exe scripts\check_gpu.py` |

## 开发环境（不改代码可以跳过）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1   rem venv + 依赖 + ffmpeg + 模型
.venv\Scripts\python.exe scripts\selftest_offline.py            rem 离线自测（不需要网络/模型）
.venv\Scripts\python.exe -m livecap.gui                         rem 开发模式跑 GUI
.venv\Scripts\python.exe scripts\preview_overlay.py             rem 渲染字幕条预览图（检查排版，不占屏幕）
.venv\Scripts\python.exe scripts\convert_mt_model.py            rem 重新转换翻译模型
.venv\Scripts\python.exe scripts\test_mt.py                     rem 对比翻译质量/耗时
```
## 致谢

感谢我们的开发者——没吃太多白饭的蓝色大肥鱼deepseek
