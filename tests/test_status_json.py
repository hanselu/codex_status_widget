from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from codex_widget.models import CodexSnapshot, HookSignal, QuotaWindow
from codex_widget import status_json
import main


def snapshot(status='working'):
    return CodexSnapshot(
        status=status, status_text={'idle': '闲置中', 'working': '工作中', 'waiting': '待确认',
                                   'cooldown': '无额度', 'offline': '未运行'}[status],
        primary_title='5小时', primary=QuotaWindow(used_percent=25, window_minutes=300),
        primary_text='', primary_visible=True,
        secondary_title='周额度', secondary=QuotaWindow(
            used_percent=100, window_minutes=10080,
            resets_at=datetime(2026, 10, 12, tzinfo=timezone.utc)),
        secondary_text='', secondary_visible=True, reset_text='', updated_text='12:00:00',
        note='工作 × 2', detail='工作中\n- 示例项目', codex_app_running=True,
        hook_signal=HookSignal(status='working', working_count=2, waiting_count=1,
                               background_count=1, last_event_name='PreToolUse',
                               last_event_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
                               session_id='session-1', turn_id='turn-1', cwd='D:/Project/示例',
                               model='example-model'),
    )


@pytest.mark.parametrize('status', ['idle', 'working', 'waiting', 'cooldown', 'offline'])
def test_json_contract_preserves_status_and_known_data(tmp_path, status):
    value = snapshot(status)
    path = tmp_path / 'export' / 'status.json'
    result = status_json.save_status_json(value, path)
    payload = json.loads(path.read_text(encoding='utf-8'))

    assert payload == result
    assert payload['schema_version'] == 1
    assert payload['status'] == status
    assert payload['status_text'] == value.status_text
    assert payload['codex_app_running'] is True
    assert datetime.fromisoformat(payload['updated_at']).tzinfo is not None
    assert payload['tasks'] == {'working_count': 2, 'waiting_count': 1, 'background_count': 1}
    assert payload['note'] == value.note
    assert payload['detail'] == value.detail
    assert payload['last_event'] == {
        'name': 'PreToolUse', 'at': '2026-10-11T00:00:00+00:00',
        'session_id': 'session-1', 'turn_id': 'turn-1',
        'cwd': 'D:/Project/示例', 'model': 'example-model',
    }
    assert payload['quotas'] == [
        {'title': '5小时', 'window_minutes': 300, 'used_percent': 25,
         'remaining_percent': 75, 'resets_at': None},
        {'title': '周额度', 'window_minutes': 10080, 'used_percent': 100,
         'remaining_percent': 0, 'resets_at': '2026-10-12T00:00:00+00:00'},
    ]
    assert '示例项目' in path.read_text(encoding='utf-8')


def test_unknown_fields_stay_null_and_hidden_quota_is_omitted():
    value = replace(snapshot(), primary=QuotaWindow(), primary_title='额度',
                    secondary_visible=False, hook_signal=HookSignal(), codex_app_running=None)
    payload = status_json.snapshot_to_dict(value)

    assert payload['codex_app_running'] is None
    assert payload['last_event']['at'] is None
    assert payload['quotas'] == [{'title': '额度', 'window_minutes': None,
                                 'used_percent': None, 'remaining_percent': None,
                                 'resets_at': None}]


def test_weekly_only_uses_actual_window_instead_of_slot_name():
    value = replace(snapshot(), primary=snapshot().secondary,
                    primary_title='周额度', secondary_visible=False)
    payload = status_json.snapshot_to_dict(value)
    assert len(payload['quotas']) == 1
    assert payload['quotas'][0]['window_minutes'] == 10080
    assert payload['quotas'][0]['title'] == '周额度'


def test_atomic_export_keeps_previous_json_until_commit(tmp_path, monkeypatch):
    path = tmp_path / 'status.json'
    status_json.save_status_json(snapshot('working'), path)
    previous = path.read_bytes()
    original = status_json.QSaveFile
    observed = []

    def intercept(filename):
        target = original(filename)
        commit = target.commit

        def checked_commit():
            observed.append(path.read_bytes())
            return commit()

        target.commit = checked_commit
        return target

    monkeypatch.setattr(status_json, 'QSaveFile', intercept)
    status_json.save_status_json(snapshot('idle'), path)

    assert observed == [previous]
    assert json.loads(path.read_text(encoding='utf-8'))['status'] == 'idle'
    assert list(tmp_path.iterdir()) == [path]


def test_failed_commit_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / 'status.json'
    status_json.save_status_json(snapshot(), path)
    previous = path.read_bytes()
    original = status_json.QSaveFile

    def failing(filename):
        target = original(filename)
        commit = target.commit

        def fail_commit():
            target.cancelWriting()
            return commit()

        target.commit = fail_commit
        return target

    monkeypatch.setattr(status_json, 'QSaveFile', failing)
    with pytest.raises(OSError, match='JSON'):
        status_json.save_status_json(snapshot('offline'), path)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_json_once_writes_same_payload_as_stdout_without_window(tmp_path, monkeypatch, capsys):
    from codex_widget.config import AppConfig

    monkeypatch.setattr(main.AppConfig, 'load', lambda: AppConfig.default())
    monkeypatch.setattr(main, 'read_snapshot', lambda config: snapshot())
    monkeypatch.setattr('codex_widget.config.CONFIG_DIR', tmp_path)
    monkeypatch.setattr('sys.argv', ['main.py', '--json-once'])

    assert main.main() == 0
    output = capsys.readouterr().out
    assert output.isascii()
    assert json.loads(output) == json.loads(
        (tmp_path / 'status.json').read_text(encoding='utf-8'))


def test_json_once_reports_save_failure(tmp_path, monkeypatch, capsys):
    from codex_widget.config import AppConfig

    monkeypatch.setattr(main.AppConfig, 'load', lambda: AppConfig.default())
    monkeypatch.setattr(main, 'read_snapshot', lambda config: snapshot())
    monkeypatch.setattr('codex_widget.config.CONFIG_DIR', tmp_path)

    def fail_save(*args):
        raise OSError('simulated write error')

    monkeypatch.setattr(status_json, 'save_status_json', fail_save)
    assert main.json_once() == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'JSON 导出失败' in captured.err
