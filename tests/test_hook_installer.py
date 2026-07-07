from __future__ import annotations

import json
from pathlib import Path
import sys

from codex_widget import hook_installer


def test_install_removes_old_widget_hooks(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    hooks_path = tmp_path / '.codex' / 'hooks.json'
    writer_path = tmp_path / '.codex_widget' / 'hook_writer.py'
    config_dir = tmp_path / '.codex_widget'
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                'hooks': {
                    'PreToolUse': [
                        {
                            'matcher': '.*',
                            'hooks': [
                                {
                                    'type': 'command',
                                    'commandWindows': f'py -3 "{writer_path}"',
                                    'statusMessage': 'Codex Widget: record status',
                                }
                            ],
                        }
                    ],
                    'PostToolUse': [
                        {
                            'matcher': '.*',
                            'hooks': [{'type': 'command', 'commandWindows': 'py -3 "other.py"'}],
                        }
                    ],
                }
            }
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(hook_installer, 'CODEX_HOOKS_PATH', hooks_path)
    monkeypatch.setattr(hook_installer, 'HOOK_WRITER_PATH', writer_path)
    monkeypatch.setattr(hook_installer, 'CONFIG_DIR', config_dir)

    hook_installer.install_hooks()

    data = json.loads(hooks_path.read_text(encoding='utf-8'))
    hooks = data['hooks']
    assert set(hooks) == {
        'PermissionRequest',
        'PostToolUse',
        'PreToolUse',
        'Stop',
        'SubagentStart',
        'SubagentStop',
        'UserPromptSubmit',
    }
    assert hooks['PostToolUse'][0]['hooks'][0]['commandWindows'] == 'py -3 "other.py"'
    assert len(hooks['UserPromptSubmit']) == 1
    assert len(hooks['Stop']) == 1

    status = hook_installer.read_hook_setup_status()
    assert status.is_complete
    assert not status.missing_events


def test_install_copies_hook_writer_from_pyinstaller_bundle(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    hooks_path = tmp_path / '.codex' / 'hooks.json'
    writer_path = tmp_path / '.codex_widget' / 'hook_writer.py'
    config_dir = tmp_path / '.codex_widget'
    bundle_dir = tmp_path / '_MEI12345'
    bundled_writer = bundle_dir / 'codex_widget' / 'hook_writer.py'
    bundled_writer.parent.mkdir(parents=True)
    bundled_writer.write_text('# bundled hook writer\n', encoding='utf-8')

    monkeypatch.setattr(hook_installer, 'CODEX_HOOKS_PATH', hooks_path)
    monkeypatch.setattr(hook_installer, 'HOOK_WRITER_PATH', writer_path)
    monkeypatch.setattr(hook_installer, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(hook_installer, '__file__', str(tmp_path / 'missing' / 'hook_installer.py'))
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle_dir), raising=False)

    hook_installer.install_hooks()

    assert writer_path.read_text(encoding='utf-8') == '# bundled hook writer\n'


def test_hook_setup_status_reports_missing_events(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    hooks_path = tmp_path / '.codex' / 'hooks.json'
    writer_path = tmp_path / '.codex_widget' / 'hook_writer.py'
    config_dir = tmp_path / '.codex_widget'
    hooks_path.parent.mkdir(parents=True)
    writer_path.parent.mkdir(parents=True)
    writer_path.write_text('# installed hook writer\n', encoding='utf-8')
    hooks_path.write_text(
        json.dumps(
            {
                'hooks': {
                    'UserPromptSubmit': [
                        {
                            'hooks': [
                                {
                                    'type': 'command',
                                    'commandWindows': f'py -3 "{writer_path}"',
                                }
                            ]
                        }
                    ],
                    'Stop': [
                        {
                            'hooks': [
                                {
                                    'type': 'command',
                                    'commandWindows': f'py -3 "{writer_path}"',
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(hook_installer, 'CODEX_HOOKS_PATH', hooks_path)
    monkeypatch.setattr(hook_installer, 'HOOK_WRITER_PATH', writer_path)
    monkeypatch.setattr(hook_installer, 'CONFIG_DIR', config_dir)

    status = hook_installer.read_hook_setup_status()

    assert not status.is_complete
    assert status.writer_exists
    assert set(status.installed_events) == {'UserPromptSubmit', 'Stop'}
    assert set(status.missing_events) == {
        'PermissionRequest',
        'PreToolUse',
        'PostToolUse',
        'SubagentStart',
        'SubagentStop',
    }
