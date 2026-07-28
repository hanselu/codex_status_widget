# Codex 状态与额度小挂件

Windows 桌面小挂件，用 Python + PySide6 显示 ChatGPT 桌面应用中 Codex 的多状态指示灯和订阅额度文本。

这一版把“状态”和“额度”彻底分开：

- **工作状态**：通过 Codex Hooks 主动上报到 `%USERPROFILE%\.codex_widget\hook_events.jsonl`
- **额度信息**：优先读取 Codex 本地日志里的 `codex.rate_limits`，再回退到 `%USERPROFILE%\.codex\sessions\**\*.jsonl` 里的 `token_count` 事件

这样可以避免旧版用 session 文件修改时间判断状态时，长时间任务被误判成绿色的问题。

## 状态灯规则

| 颜色 | 状态 | 规则 |
|---|---|---|
| 绿色 | 闲置 | Hook 收到 `Stop`、transcript 已记录 `task_complete`，或没有活跃 turn |
| 蓝色 | 工作中 | 有活跃 turn，包括生成响应、工具执行或子任务运行 |
| 橙色 | 待确认 | Hook 收到 `PermissionRequest`，或 transcript 中出现尚未完成的 `require_escalated` 工具调用 |
| 红色 | 无额度 | 额度达到 100% 且重置时间仍在未来，或检测到明确 cooldown / quota / rate limit 错误 |
| 红色 | 未运行 | ChatGPT App 进程未运行 |

多对话同时运行时，主状态灯按优先级聚合：红色异常 > 待确认 > 工作中 > 闲置。小挂件正文只显示数量摘要，托盘和悬停提示显示具体对话明细。

除“无额度”会触发红灯外，额度百分比不影响工作状态。

## 安装依赖

```powershell
cd codex_status_widget
uv sync
```

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
- 没有 `transcript_path` 的活跃事件无法二次确认完成状态，会在 30 秒后视为闲置，避免新版 ChatGPT App 中 Codex 的孤儿事件长期保持状态灯活跃。
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
- 挂件面板的常规额度读取只使用本地 Codex 日志和 `token_count`；只有用户主动点击“查询重置额度”时才读取本机 `access_token` 并请求 ChatGPT。程序不读取 Cookie、不会显示或持久化 token。
