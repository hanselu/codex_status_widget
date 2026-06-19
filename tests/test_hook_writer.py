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
