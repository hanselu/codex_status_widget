from __future__ import annotations

import json
from pathlib import Path

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
    assert set(hooks) == {'PostToolUse', 'UserPromptSubmit', 'Stop'}
    assert hooks['PostToolUse'][0]['hooks'][0]['commandWindows'] == 'py -3 "other.py"'
    assert len(hooks['UserPromptSubmit']) == 1
    assert len(hooks['Stop']) == 1
