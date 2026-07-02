from __future__ import annotations

import json
from pathlib import Path

from codex_widget import hook_writer


def test_hook_writer_trims_large_event_file(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    events_path = tmp_path / 'hook_events.jsonl'
    monkeypatch.setattr(hook_writer, 'MAX_EVENT_BYTES', 600)
    monkeypatch.setattr(hook_writer, 'MAX_EVENTS_TO_KEEP', 3)

    for index in range(10):
        hook_writer._append_jsonl(
            events_path,
            {
                'recorded_at': '2026-06-19T00:00:00+00:00',
                'hook_event_name': 'UserPromptSubmit',
                'session_id': str(index),
                'padding': 'x' * 80,
            },
        )

    lines = events_path.read_text(encoding='utf-8').splitlines()
    session_ids = [json.loads(line)['session_id'] for line in lines]

    assert len(lines) == 3
    assert session_ids == ['7', '8', '9']
    assert events_path.stat().st_size <= 600


def test_hook_writer_ignores_non_json_payload() -> None:
    assert hook_writer._safe_loads('not json') is None


def test_hook_writer_normalizes_camel_case_payload() -> None:
    event = hook_writer._normalize_event(
        {
            'hookEventName': 'Stop',
            'sessionId': 's1',
            'turnId': 't1',
            'transcriptPath': 'C:/tmp/rollout.jsonl',
            'permissionMode': 'never',
        }
    )

    assert event is not None
    assert event['hook_event_name'] == 'Stop'
    assert event['session_id'] == 's1'
    assert event['turn_id'] == 't1'
    assert event['transcript_path'] == 'C:/tmp/rollout.jsonl'
    assert event['permission_mode'] == 'never'
