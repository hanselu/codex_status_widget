# Codex 状态与额度小挂件

Windows 桌面小挂件，用 Python + PySide6 显示 ChatGPT 桌面应用中 Codex 的多状态指示灯和订阅额度文本。

这一版把“状态”和“额度”彻底分开：

- **工作状态**：通过 Codex Hooks 主动上报到 `%USERPROFILE%\.codex_widget\hook_events.jsonl`
- **额度信息**：优先通过独立 Codex app-server 实时查询主额度；实时查询不可用时，再回退到 Codex 本地日志里的 `codex.rate_limits`、`%USERPROFILE%\.codex\sessions\**\*.jsonl` 里的 `token_count` 事件和同账号缓存

这样可以避免旧版用 session 文件修改时间判断状态时，长时间任务被误判成绿色的问题。

0.3.9 修正了状态刷新延迟和任务结束识别：界面每 2～3 秒读取一次工作状态，额度查询在后台每至少 60 秒执行一次，网络等待不会阻塞状态灯。归档后的日志会从 `archived_sessions` 中继续查找；没有日志路径的 Hook 也会尝试按 session id 找回日志。对于没有 `Stop` 的临时会话，还会只读检查 `logs_2.sqlite` 中同一会话的明确关闭记录。

常规额度查询会优先启动 `%USERPROFILE%\.codex\plugins\.plugin-appserver\codex.exe app-server --stdio`，并按 `initialize`、`initialized`、`account/read`、`account/rateLimits/read` 顺序读取实时额度。返回多个额度池时只优先使用 `limitId == "codex"` 的主额度池，避免误用 `codex_bengalfox` 等模型专属池。若 app-server 不存在、未登录、认证失败、网络失败、协议变化、超时或返回结构不可识别，小挂件不会把这些调试细节显示到面板上，而是自动沿用本地日志、session 或同账号缓存读取结果。程序不会显示、记录或缓存 token、完整账号 ID、邮箱或认证头。

## 状态灯规则

| 颜色 | 状态 | 规则 |
|---|---|---|
| 绿色 | 闲置 | Hook 收到 `Stop`、当前或归档 transcript 已记录 `task_complete` / `turn_aborted`、Codex 明确记录会话关闭，或没有活跃 turn |
| 蓝色 | 工作中 | 有活跃 turn，包括生成响应、工具执行或子任务运行 |
| 橙色 | 待确认 | Hook 收到 `PermissionRequest`，或 transcript 中出现尚未完成的 `require_escalated` 工具调用 |
| 红色 | 无额度 | 额度达到 100% 且重置时间仍在未来，或检测到明确 cooldown / quota / rate limit 错误 |
| 红色 | 未运行 | ChatGPT App 进程未运行 |

多对话同时运行时，主状态灯按优先级聚合：红色异常 > 待确认 > 工作中 > 闲置。小挂件正文只显示数量摘要，托盘和悬停提示显示具体对话明细。

托盘图标的外圈使用状态灯颜色，中央显示剩余额度的整数百分比：优先显示 5 小时额度，没有可用的 5 小时额度时显示周额度；两者都未读取到时显示 `--`。

0.4.0 放大了托盘图标中央的额度数字。

0.4.1 修复后台任务结束后仍显示蓝灯的问题：当 Hook 与运行日志的会话编号不一致时，通过任务轮次编号关联明确的关闭记录。

0.5.0 新增 GEM12 屏幕图片生成与后台推送。

0.5.1 将屏幕控制改为使用 PyPI 的 `gem12-screen` 库，不再维护项目内的屏控代码副本。

除“无额度”会触发红灯外，额度百分比不影响工作状态。

## 安装依赖

需要 Python 3.12 或更高版本。`uv sync` 会从 PyPI 安装
[`gem12-screen`](https://pypi.org/project/gem12-screen/)，由该库提供屏幕连接、串口检测和
图像传输协议。本项目只维护状态图片生成与推送逻辑，不再保留屏控代码副本，
安装、运行、测试和打包均不依赖原屏控项目目录。
Pillow 用于本项目的图片转换，仍作为直接依赖；pyserial 由 `gem12-screen` 引入。
项目已将 `gem12-screen` 的安装源指定为官方 PyPI，避免镜像尚未同步新库时无法安装。

```powershell
cd codex_status_widget
uv sync
```

## GEM12 屏幕图片与实时推送

右键小挂件或托盘图标，勾选 **推送至 GEM12 屏幕**。该开关会保存到配置，
之后启动程序时自动恢复。旧配置默认关闭推送，启用前请退出占用串口的官方 AOOSTAR-X。
串口按屏幕 VID/PID 自动识别，不固定为 COM3。

启用后，程序复用挂件的同一份状态与额度数据，绘制 960×376 深色仪表图片：

- 只有周额度：左侧状态、右侧周额度。
- 同时有 5 小时和周额度：左侧状态、中间 5 小时、右侧周额度。
- 状态灯位于状态文字左侧；环形仪表和百分比都表示剩余额度。
- 未读取的数据用 `--`，0% 表示确实没有剩余额度；未知窗口使用中性名称。
- 暂时查询失败沿用原有额度读取器的日志/同账号缓存回退，不把查询失败当作 0%。

工作状态沿用最多 3 秒一次的检查，额度沿用后台至少 60 秒一次的查询。
只有画面内容变化时才生成新图并推送；传输过程中只保留最新待发送画面，不累计历史帧。
`gem12-screen` 首次发送整帧，后续默认只发送变化的数据块；实际屏幕更新仍需加上串口传输耗时。

最新图片位于 `%USERPROFILE%\.codex_widget\gem12-status.png`，使用完整写入后替换的方式保存。
PNG 保存、RGB565 编码和串口传输在独立后台线程完成，不阻塞挂件刷新。
菜单的“屏幕：…”一行显示推送结果；连接或传输失败时关闭连接，约 15 秒后在下一次状态检查重试。
取消勾选或退出程序会在正在发送的画面结束后释放串口，保留屏幕最后画面，不主动关屏。

也可只生成并推送一次真实状态，不启动挂件，也不修改推送开关：

```powershell
uv run python main.py --screen-once
```

单次命令与持续推送不能同时占用屏幕串口。源代码更新不会修改已经运行的旧 EXE；
使用新功能需退出旧版后运行 `uv run python main.py`，或重新打包并启动新版。

可选的配置段（通常只需通过菜单切换）：

```toml
[screen]
enabled = false
port = ""
```

`port` 留空表示自动识别；检测到多个目标设备时，可以填写其中一个目标串口。

## 安装 Codex Hook

```powershell
.\install_hook.ps1
```

或者：

```powershell
uv run python main.py --install-hook
```

安装脚本会：

1. 复制独立的 `hook_writer.py` 到 `%USERPROFILE%\.codex_widget\hook_writer.py`
2. 合并更新 `%USERPROFILE%\.codex\hooks.json`
3. 自动备份原有 `hooks.json`

安装后，需要在 ChatGPT App 的 Codex 中打开：

```text
/hooks
```

然后 review / trust 新增的 hook。未 trust 之前，Codex 会跳过这个 hook，小挂件会退回到 session 文件修改时间的保守判断。

## 控制台验证

```powershell
.\check_once.ps1
```

或者：

```powershell
uv run python main.py --once
```

正常时会看到类似：

额度行会根据服务端返回的窗口时长自适应显示。只有周额度时显示一行；同时返回 5 小时和周额度时显示两行；未知时长使用中性名称。

```text
状态：待确认
周额度：97% 7-21 20:22
提示：待确认 × 1 · 工作 × 2
```

## 启动小挂件

```powershell
.\run_widget.ps1
```

或者：

```powershell
uv run python main.py
```

## 打包 exe

推荐使用项目根目录的一键打包脚本：

```powershell
.\package.ps1
```

脚本会依次执行：

1. 检查 `uv`
2. 运行 `uv run pytest`
3. 停止正在运行的 `codex_status_widget.exe`
4. 删除旧的 `build` / `dist`
5. 使用 `codex_status_widget.spec` 强制重新打包
6. 输出 exe 路径、大小、更新时间和 SHA256

常用参数：

```powershell
.\package.ps1 -SkipTests
.\package.ps1 -LaunchAfterBuild
.\package.ps1 -SkipTests -LaunchAfterBuild
```

如果 PowerShell 执行策略拦截脚本，可以用：

```powershell
powershell -ExecutionPolicy Bypass -File .\package.ps1
```

也可以手动执行打包命令：

```powershell
uv run --with pyinstaller pyinstaller --noconfirm --clean codex_status_widget.spec
```

必须使用项目根目录的 `codex_status_widget.spec` 打包；它会把 `codex_widget\hook_writer.py` 一起打进 exe。否则打包版右键“添加钩子到 Codex”时无法复制 hook writer。

## 右键菜单

- 刷新
- 查询重置额度
- 标记为闲置
- 锁定位置 / 解锁位置
- 添加钩子到 Codex
- 打开 sessions 目录
- 打开状态目录
- 版本：当前版本号
- 退出

`标记为闲置` 用于 ChatGPT App 异常退出、Hook 没有收到 `Stop`、或者你手动想把黄灯重置为绿灯的情况。

`查询重置额度` 只在点击时读取 Codex sessions 配置目录同级的 `auth.json`（默认为 `%USERPROFILE%\.codex\auth.json`）中的 `access_token`，向 ChatGPT 查询可用重置额度，并用消息框显示状态、标题以及换算为本地时间的发放/过期时间。查询结果不会显示在挂件面板中。

`添加钩子到 Codex` 会复制 hook writer 并合并更新 `%USERPROFILE%\.codex\hooks.json`。添加后仍需要在 ChatGPT App 的 Codex 中打开 `/hooks` 并 review / trust 新 hook。

## 卸载 Hook

```powershell
.\uninstall_hook.ps1
```

或者：

```powershell
uv run python main.py --uninstall-hook
```

这只会从 `%USERPROFILE%\.codex\hooks.json` 移除本工具添加的 hook，并备份原文件；不会删除你的其他 hook。

## 配置文件

首次运行后会生成：

```text
%USERPROFILE%\.codex_widget\config.toml
```

默认配置：

```toml
[codex]
sessions_dir = "C:\\Users\\Hanse\\.codex\\sessions"

[hook]
events_path = "C:\\Users\\Hanse\\.codex_widget\\hook_events.jsonl"
stale_after_minutes = 360
max_events_to_read = 5000

[ui]
x = -1
y = -1
width = 280
height = 142
opacity = 0.92
locked = false

[status]
working_window_seconds = 60
refresh_interval_seconds = 5
```

说明：

- 安装 `UserPromptSubmit`、`PermissionRequest`、`PreToolUse`、`PostToolUse`、`SubagentStart`、`SubagentStop` 和 `Stop`，用于区分工作、待确认和闲置。
- `hook_events.jsonl` 超过约 256KB 时会自动压缩，只保留最近 500 条事件。
- `stale_after_minutes` 是 Hook 没收到 `Stop` 且 transcript 也没有完成记录时的兜底过期时间，默认 360 分钟。
- 没有 `transcript_path` 时，不再在工具结束或 30 秒后直接视为闲置；先尝试找回日志，并检查同一会话的关闭记录。所有结束信号都缺失时，仍按 `stale_after_minutes` 兜底，无法仅凭静默时间确认任务已完成。
- 后台任务的 Hook 会话编号与关闭日志编号不同时，通过完整的任务轮次编号关联；只有唯一匹配且关闭时间不早于最后一次活动时，才恢复闲置。
- `refresh_interval_seconds` 为兼容旧配置保留；状态检查间隔最多 3 秒，后台额度查询间隔为 `max(60, refresh_interval_seconds)` 秒。右键“刷新”会立即请求更新额度，已有查询进行中时不会并发启动新查询。
- `working_window_seconds` 只在 Hook 没安装或没被 trust 时作为回退判断使用。

## 文件说明

```text
codex_status_widget/
├─ main.py
├─ pyproject.toml
├─ README.md
├─ package.ps1
├─ run_widget.ps1
├─ check_once.ps1
├─ install_hook.ps1
├─ uninstall_hook.ps1
├─ codex_widget/
│  ├─ app.py
│  ├─ ui.py
│  ├─ config.py
│  ├─ models.py
│  ├─ quota_reader.py
│  ├─ hook_writer.py
│  ├─ hook_state.py
│  ├─ hook_installer.py
│  └─ snapshot.py
└─ tests/
```

## 设计取舍

- 桌面应用检测精确识别 `ChatGPT.exe`，同时兼容旧版 `Codex.exe`；小写 `codex.exe` 是后台代理，不作为桌面应用。
- Hook writer 不保存 prompt、tool input、tool output，只保存事件名、session id、turn id、cwd、model 等生命周期字段。
- Hook writer 不输出 stdout，避免影响 Codex 上下文。
- Hook writer 异常时返回 0，避免因为小挂件故障阻塞 Codex。
- 挂件面板的常规额度读取优先使用独立 Codex app-server 实时查询；实时查询失败时回退到本地 Codex 日志、`token_count` 和同账号缓存。只有用户主动点击“查询重置额度”时才读取本机 `access_token` 并请求 ChatGPT。程序不读取 Cookie、不会显示或持久化 token。
