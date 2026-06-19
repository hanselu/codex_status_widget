from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from typing import Any

from .config import CODEX_HOOKS_PATH, CONFIG_DIR, HOOK_WRITER_PATH


EVENTS_WITHOUT_MATCHER = ('UserPromptSubmit', 'Stop')
EVENTS_WITH_MATCHER: tuple[str, ...] = ()
WIDGET_MARKER = 'codex_widget/hook_writer'
STATUS_MESSAGE = 'Codex Widget: record status'


def install_hooks() -> tuple[Path, Path, Path | None]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _copy_hook_writer()
    backup_path = _merge_hooks_json()
    return HOOK_WRITER_PATH, CODEX_HOOKS_PATH, backup_path


def uninstall_hooks() -> tuple[Path, Path | None]:
    if not CODEX_HOOKS_PATH.exists():
        return CODEX_HOOKS_PATH, None

    backup_path = _backup_file(CODEX_HOOKS_PATH)
    data = _read_hooks_json(CODEX_HOOKS_PATH)
    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        return CODEX_HOOKS_PATH, backup_path

    _remove_widget_hooks(hooks)

    _write_json(CODEX_HOOKS_PATH, data)
    return CODEX_HOOKS_PATH, backup_path


def _copy_hook_writer() -> None:
    source = Path(__file__).with_name('hook_writer.py')
    shutil.copy2(source, HOOK_WRITER_PATH)


def _merge_hooks_json() -> Path | None:
    CODEX_HOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_file(CODEX_HOOKS_PATH) if CODEX_HOOKS_PATH.exists() else None
    data = _read_hooks_json(CODEX_HOOKS_PATH)

    hooks = data.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        hooks = {}
        data['hooks'] = hooks

    _remove_widget_hooks(hooks)
    for event_name in EVENTS_WITHOUT_MATCHER:
        _append_widget_hook(hooks, event_name, matcher=None)
    for event_name in EVENTS_WITH_MATCHER:
        _append_widget_hook(hooks, event_name, matcher='.*')

    _write_json(CODEX_HOOKS_PATH, data)
    return backup_path


def _append_widget_hook(hooks: dict[str, Any], event_name: str, matcher: str | None) -> None:
    blocks = hooks.setdefault(event_name, [])
    if not isinstance(blocks, list):
        blocks = []
        hooks[event_name] = blocks

    # Remove prior widget blocks first to avoid duplicates after reinstall.
    cleaned = [_clean_block(block) for block in blocks if isinstance(block, dict)]
    blocks[:] = [block for block in cleaned if block is not None]

    block: dict[str, Any] = {
        'hooks': [_make_hook_command()],
    }
    if matcher is not None:
        block['matcher'] = matcher

    blocks.append(block)


def _remove_widget_hooks(hooks: dict[str, Any]) -> None:
    for event_name in list(hooks.keys()):
        blocks = hooks.get(event_name)
        if not isinstance(blocks, list):
            continue
        cleaned = [_clean_block(block) for block in blocks if isinstance(block, dict)]
        cleaned = [block for block in cleaned if block is not None]
        if cleaned:
            hooks[event_name] = cleaned
        else:
            hooks.pop(event_name, None)


def _clean_block(block: dict[str, Any]) -> dict[str, Any] | None:
    raw_hooks = block.get('hooks')
    if not isinstance(raw_hooks, list):
        return block

    kept = []
    for hook in raw_hooks:
        if not isinstance(hook, dict):
            kept.append(hook)
            continue
        command_text = ' '.join(
            str(hook.get(key, ''))
            for key in ('command', 'commandWindows', 'command_windows', 'statusMessage')
        )
        if WIDGET_MARKER in command_text or 'hook_writer.py' in command_text and '.codex_widget' in command_text:
            continue
        kept.append(hook)

    if not kept:
        return None

    copied = dict(block)
    copied['hooks'] = kept
    return copied


def _make_hook_command() -> dict[str, Any]:
    writer = str(HOOK_WRITER_PATH)
    win_cmd = f'py -3 "{writer}"'
    # `command` is kept for portability; `commandWindows` is used on Windows.
    return {
        'type': 'command',
        'command': f'python3 "{writer}"',
        'commandWindows': win_cmd,
        'statusMessage': STATUS_MESSAGE,
    }


def _read_hooks_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {'hooks': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'hooks': {}}
    if isinstance(data, dict):
        return data
    return {'hooks': {}}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = path.with_name(f'{path.name}.bak.{stamp}')
    shutil.copy2(path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if argv and argv[0] == '--uninstall':
        hooks_path, backup_path = uninstall_hooks()
        print(f'已移除 Codex Widget hooks：{hooks_path}')
        if backup_path:
            print(f'已备份原 hooks.json：{backup_path}')
        return 0

    writer_path, hooks_path, backup_path = install_hooks()
    print(f'已安装 hook writer：{writer_path}')
    print(f'已更新 Codex hooks：{hooks_path}')
    if backup_path:
        print(f'已备份原 hooks.json：{backup_path}')
    print('下一步：在 Codex 里打开 /hooks，review/trust 新 hook。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
