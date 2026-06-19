# Codex 状态与额度小挂件

Windows 桌面小挂件，用 Python + PySide6 显示 Codex 桌面端的三色工作状态和订阅额度文本。

这一版把“状态”和“额度”彻底分开：

- **工作状态**：通过 Codex Hooks 主动上报到 `%USERPROFILE%\.codex_widget\hook_events.jsonl`
- **额度信息**：继续读取 `%USERPROFILE%\.codex\sessions\**\*.jsonl` 里的 `token_count` 事件

这样可以避免旧版用 session 文件修改时间判断状态时，长时间思考被误判成绿色的问题。

## 状态灯规则

| 颜色 | 状态 | 规则 |
|---|---|---|
| 绿色 | 闲置 | Hook 收到 `Stop`，或没有工作中的 turn |
| 黄色 | 正在干活 | Hook 收到 `UserPromptSubmit`，且还没收到 `Stop` |
| 红色 | 无额度 / Codex 未运行 | 额度达到 100% 且重置时间仍在未来、检测到明确 cooldown / quota / rate limit 错误，或 Codex App 进程未运行 |

除“无额度”会触发红灯外，额度百分比不影响黄/绿工作状态。

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

安装后，需要在 Codex 里打开：

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

```text
状态：Codex 正在干活
5小时：29% 已用 / 71% 剩余
周额度：54% 已用 / 46% 剩余
重置：04:22 / 周五 00:18
更新：01:14:13
提示：hook 工作中: UserPromptSubmit 01:14:10
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

```powershell
uv run --with pyinstaller pyinstaller --noconfirm codex_status_widget.spec
```

必须使用项目根目录的 `codex_status_widget.spec` 打包；它会把 `codex_widget\hook_writer.py` 一起打进 exe。否则打包版右键“添加钩子到 Codex”时无法复制 hook writer。

## 右键菜单

- 刷新
- 标记为闲置
- 锁定位置 / 解锁位置
- 添加钩子到 Codex
- 打开 sessions 目录
- 打开状态目录
- 退出

`标记为闲置` 用于 Codex 崩溃、Hook 没有收到 `Stop`、或者你手动想把黄灯重置为绿灯的情况。

`添加钩子到 Codex` 会复制 hook writer 并合并更新 `%USERPROFILE%\.codex\hooks.json`。添加后仍需要在 Codex 里打开 `/hooks` 并 review / trust 新 hook。

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

- 只安装 `UserPromptSubmit` 和 `Stop` 两个 Hook，避免工具调用事件频繁写入状态文件。
- `hook_events.jsonl` 超过约 256KB 时会自动压缩，只保留最近 500 条事件。
- `stale_after_minutes` 是 Hook 没收到 `Stop` 时的兜底过期时间，默认 360 分钟。
- `working_window_seconds` 只在 Hook 没安装或没被 trust 时作为回退判断使用。

## 文件说明

```text
codex_status_widget/
├─ main.py
├─ pyproject.toml
├─ README.md
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

- Hook writer 不保存 prompt、tool input、tool output，只保存事件名、session id、turn id、cwd、model 等生命周期字段。
- Hook writer 不输出 stdout，避免影响 Codex 上下文。
- Hook writer 异常时返回 0，避免因为小挂件故障阻塞 Codex。
- 额度读取仍然沿用本地 `token_count`，不使用 API Key、不读取 ChatGPT Cookie。
