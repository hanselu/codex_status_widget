from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile

from .models import CodexSnapshot


def snapshot_to_dict(snapshot: CodexSnapshot) -> dict:
    """导出已有快照，不推测任务名称、执行阶段或运行时长。"""
    signal = snapshot.hook_signal
    quotas = []
    for title, window, visible in (
        (snapshot.primary_title, snapshot.primary, snapshot.primary_visible),
        (snapshot.secondary_title, snapshot.secondary, snapshot.secondary_visible),
    ):
        if visible:
            quotas.append({
                'title': title,
                'window_minutes': window.window_minutes,
                'used_percent': window.used_percent,
                'remaining_percent': window.remaining_percent,
                'resets_at': window.resets_at.isoformat() if window.resets_at is not None else None,
            })

    return {
        'schema_version': 1,
        'updated_at': datetime.now().astimezone().isoformat(),
        'status': snapshot.status,
        'status_text': snapshot.status_text,
        'codex_app_running': snapshot.codex_app_running,
        'tasks': {
            'working_count': signal.working_count,
            'waiting_count': signal.waiting_count,
            'background_count': signal.background_count,
        },
        'note': snapshot.note,
        'detail': snapshot.detail,
        'last_event': {
            'name': signal.last_event_name,
            'at': signal.last_event_at.isoformat() if signal.last_event_at is not None else None,
            'session_id': signal.session_id,
            'turn_id': signal.turn_id,
            'cwd': signal.cwd,
            'model': signal.model,
        },
        'quotas': quotas,
    }


def save_status_json(snapshot: CodexSnapshot, path: Path) -> dict:
    payload = snapshot_to_dict(snapshot)
    content = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    target = QSaveFile(str(path))
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(f'无法保存状态 JSON：{target.errorString()}')
    if target.write(content) != len(content):
        error = target.errorString()
        target.cancelWriting()
        raise OSError(f'状态 JSON 写入失败：{error}')
    if not target.commit():
        raise OSError(f'无法更新状态 JSON：{target.errorString()}')
    return payload
