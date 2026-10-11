# Codex 工作状态 JSON 输出接口说明

本接口通过本地 `status.json` 文件提供 Codex 工作状态和额度快照。调用方读取并解析文件后，可以自行生成墨水屏画面、推送到设备或用于其他状态展示。

当前格式版本为 `schema_version = 1`。文件同时适用于源码版和打包后的 EXE，不需要启动 HTTP 服务。

## 文件位置和更新方式

默认输出路径：

```text
%USERPROFILE%\.codex_widget\status.json
```

例如，当前 Windows 用户为 `Hanse` 时，路径为：

```text
C:\Users\Hanse\.codex_widget\status.json
```

路径取决于运行挂件的用户主目录，与 EXE 所在目录和调用方工作目录无关。右键挂件选择“打开状态目录”可以找到文件。

| 项目 | 约定 |
|---|---|
| 文件编码 | UTF-8，无 BOM，中文直接保存 |
| 文件内容 | 一个完整的 JSON 对象；不是逐行事件日志 |
| 生成时机 | 新版挂件启动后首次刷新即生成，之后随状态刷新更新 |
| 状态刷新 | 定时间隔为 2～3 秒；实际更新还受状态读取耗时影响 |
| 额度刷新 | 后台查询间隔为 `max(60, refresh_interval_seconds)` 秒，JSON 刷新时复用最近一次可用额度 |
| 保存方式 | 完整写入临时文件后替换目标文件，读取方不会看到先清空再写入的过程 |
| 写入失败 | 保留旧文件，下次状态刷新继续尝试 |
| 挂件退出 | 保留最后一份 JSON，不删除文件，也不额外写入“挂件已退出”状态 |

调用方应每次重新打开并读取文件，不要长期持有同一个文件句柄。首次运行前、文件被删除或保存失败时，文件可能不存在；读取失败时可保留上一份画面，在下一次轮询重试。

`--json-once` 参数可在不启动挂件窗口的情况下生成一次文件，并在标准输出打印同一份数据。标准输出使用 JSON Unicode 转义，解析结果与 UTF-8 文件相同；文件保存失败时返回非零退出码。单次导出也写入上述固定路径，避免与持续运行的挂件同时写入。

## 完整示例

以下时间、任务数量和额度均为示例值。所有顶层字段及下述对象内的字段，在版本 1 中都会输出。

```json
{
  "schema_version": 1,
  "updated_at": "2026-10-11T12:00:00.123456+08:00",
  "status": "working",
  "status_text": "工作中",
  "codex_app_running": true,
  "tasks": {
    "working_count": 2,
    "waiting_count": 0,
    "background_count": 0
  },
  "note": "工作 × 2",
  "detail": "工作中\n- 示例项目",
  "last_event": {
    "name": "PreToolUse",
    "at": "2026-10-11T11:59:59+08:00",
    "session_id": "example-session",
    "turn_id": "example-turn",
    "cwd": "D:/Project/Example",
    "model": ""
  },
  "quotas": [
    {
      "title": "5小时",
      "window_minutes": 300,
      "used_percent": 25.0,
      "remaining_percent": 75.0,
      "resets_at": "2026-10-11T16:00:00+08:00"
    },
    {
      "title": "周额度",
      "window_minutes": 10080,
      "used_percent": null,
      "remaining_percent": null,
      "resets_at": null
    }
  ]
}
```

## 顶层字段

类型中的 `null` 表示该字段允许 JSON 空值；它不是字符串 `"null"`。

| 字段 | JSON 类型 | 含义 |
|---|---|---|
| `schema_version` | integer | 格式版本，当前为 `1` |
| `updated_at` | string | 本次 JSON 导出时间，ISO 8601 格式，含时区，可能包含小数秒 |
| `status` | string | 聚合后的工作状态，枚举见下表；用于程序判断 |
| `status_text` | string | 状态对应的中文文字，用于显示 |
| `codex_app_running` | boolean 或 null | 是否检测到 Codex 客户端进程；`null` 表示检测结果未知 |
| `tasks` | object | 已识别的工作、待确认和后台任务数量 |
| `note` | string | 状态与额度的摘要提示；无内容时为 `""` |
| `detail` | string | 详情文字，可以包含换行；无内容时为 `""` |
| `last_event` | object | 状态读取器选出的代表事件，含会话和轮次信息 |
| `quotas` | array of object | 当前显示的额度窗口列表，字段见额度说明 |

`note`、`detail` 和 `status_text` 是显示文字，不应作为稳定的业务判断条件。程序应使用 `status`、任务数量和额度数值判断。

## 状态枚举和判定

| `status` | `status_text` | 含义 |
|---|---|---|
| `idle` | 闲置中 | 没有活跃用户任务；只有后台记忆整理时也属于闲置 |
| `working` | 工作中 | 有活跃用户任务，或回退到最近日志活动判断为工作中 |
| `waiting` | 待确认 | 检测到未完成的权限确认请求 |
| `cooldown` | 无额度 | 检测到明确额度/限流异常，或额度耗尽且重置时间仍在未来 |
| `offline` | 未运行 | 没有明确活跃任务或后台任务信号，且未检测到客户端进程 |

多个用户任务同时运行时，待确认优先于工作中；额度异常优先于其他状态。只有后台记忆整理时，总状态仍为 `idle`，调用方可通过 `tasks.background_count` 单独显示后台活动。

`codex_app_running` 是客户端进程检测结果，不是挂件进程存活标志。即使它为 `false`，明确的用户任务信号仍可能使总状态为 `working` 或 `waiting`。显示总状态时直接使用 `status`，不自行根据这个布尔值覆盖它。

## 任务数量

| `tasks` 字段 | JSON 类型 | 含义 |
|---|---|---|
| `working_count` | integer | 已识别的工作中用户任务数量，不含待确认任务和后台记忆整理 |
| `waiting_count` | integer | 已识别的待确认用户任务数量 |
| `background_count` | integer | 已识别的后台记忆整理任务数量，不计入用户任务数量 |

用户活跃任务总数可计算为 `working_count + waiting_count`，但这个总数只反映已识别的任务。Hook 不可用而仅根据日志修改时间判断工作中时，可能出现 `status = "working"` 且两项数量均为 `0`；不要把它解释为确定没有任务。

这些数量不代表待执行队列、历史任务总数或子代理数量，也不提供逐个任务的结构化列表。

## 代表事件

| `last_event` 字段 | JSON 类型 | 含义和缺失值 |
|---|---|---|
| `name` | string | 生命周期事件名；未知时为 `""` |
| `at` | string 或 null | 事件时间，ISO 8601 格式且含时区；未知时为 `null` |
| `session_id` | string | 代表事件的会话 ID；未知时为 `""` |
| `turn_id` | string | 代表事件的任务轮次 ID；未知时为 `""` |
| `cwd` | string | 代表事件关联的工作目录；未知时为 `""` |
| `model` | string | 已采集的模型名称；未知时为 `""` |

`last_event` 是现有状态读取器选出的代表事件。多任务时通常优先代表待确认或工作任务，不保证是所有会话中时间最晚的一条事件；会话结束后也可能保留其最后事件。不要将它当作完整任务列表。

常见事件含义如下，事件名称不是封闭枚举，读取方应容忍其他名称：

| `name` 示例 | 含义 |
|---|---|
| `UserPromptSubmit` | 用户提交任务 |
| `PreToolUse` | 工具调用前 |
| `PostToolUse` | 工具调用后；不代表整个任务已结束 |
| `PermissionRequest` | 请求权限确认 |
| `TranscriptApprovalRequest` | 从会话日志识别到未完成的权限确认 |
| `SubagentStart` / `SubagentStop` | 子任务开始 / 结束 |
| `Stop` | 响应结束 |
| `WidgetManualIdle` | 用户手动标记闲置 |

本接口不输出任务名称、工具名称、提示词、工具输入输出、分析/编写/检查阶段或累计运行时长。`last_event.at` 是事件时间，不能当作任务开始时间来计算累计运行时长。

## 额度窗口

`quotas` 中每个元素的格式相同：

| 字段 | JSON 类型 | 含义 |
|---|---|---|
| `title` | string | 当前窗口的显示名称，例如 `5小时`、`周额度`、`额度`、`额外额度` |
| `window_minutes` | integer 或 null | 窗口时长，单位为分钟；未知时为 `null` |
| `used_percent` | number 或 null | 已用额度百分比，`25` 表示 25%；保留读取到的数值，未知时为 `null` |
| `remaining_percent` | number 或 null | 剩余额度百分比，计算为 `max(0, 100 - used_percent)`；已用额度未知时为 `null` |
| `resets_at` | string 或 null | 重置时间，ISO 8601 格式且含时区；未知时为 `null` |

JSON 数值可能是整数或小数，调用方不要要求小数点必须出现。百分比通常为 0～100，单位不是 0～1 比例值。

窗口识别应使用 `window_minutes`：

| 值 | 窗口含义 |
|---|---|
| `300` | 5 小时额度 |
| `10080` | 周额度 |
| 其他整数 | 其他时长窗口，可使用 `title` 显示 |
| `null` | 窗口时长未知 |

当前通常输出一个或两个窗口；仅有周额度时，数组第一个元素就是周额度。完全没有读到额度时，输出一个 `title = "额度"` 且其余四项均为 `null` 的窗口。调用方应遍历数组，不能固定把第一个元素当作 5 小时额度。

`remaining_percent = null` 表示未知，`remaining_percent = 0` 表示已用值对应的剩余额度为零，`remaining_percent = 100` 表示全部剩余。不能用“值为假”的判断把 `0` 和 `null` 合并，也不能把未知额度画成 0%。总状态是否无额度仍应使用 `status`，因为额度判定还考虑重置时间和明确的异常信号。

额度查询失败时，挂件可能沿用日志或同账号缓存中的可用结果。因此，`updated_at` 持续更新不表示额度刚刚查询成功；版本 1 没有单独输出额度查询时间或来源。

## 时间有效性和读取建议

所有非空时间都应使用支持 ISO 8601 的时间解析器读取，保留时区进行比较。不要假定偏移一定是 `+08:00`，也不要按固定字符串长度截取时间。

文件是否过期应根据 `updated_at` 判断。调用方可按自己的刷新需求设置阈值，例如超过 30 秒标记为“数据未更新”；这是调用方的显示策略，不是接口规定的状态。文件过期不等于 Codex 未运行，不能擅自改写为 `offline`。

读取流程建议：

1. 重新打开文件，按 UTF-8 读取，并解析完整 JSON。
2. 检查 `schema_version`，版本 1 按本说明解析；未知版本采用调用方的兼容或提示策略。
3. 检查 `updated_at` 是否过期，再根据 `status` 和额度字段生成画面。
4. 读取失败时保留上一份成功结果，等待下一次轮询；未知字段可忽略。

下面是一次读取的 Python 示例，不依赖本项目的 Python 模块：

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

path = Path.home() / '.codex_widget' / 'status.json'
data = json.loads(path.read_text(encoding='utf-8'))
if data['schema_version'] != 1:
    raise ValueError('不支持的 JSON 格式版本')

updated_at = datetime.fromisoformat(data['updated_at'])
stale = datetime.now(timezone.utc) - updated_at > timedelta(seconds=30)
print('数据未更新' if stale else data['status_text'])

for quota in data['quotas']:
    remaining = quota['remaining_percent']
    display = '--' if remaining is None else f'{remaining:.0f}%'
    print(quota['title'], display)
```

如果挂件和调用方在不同的 Windows 用户或不同设备上运行，调用方需使用明确指定或传输后的文件路径，不能直接套用自身的 `Path.home()`。
