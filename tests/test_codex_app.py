from __future__ import annotations

from types import SimpleNamespace

from codex_widget import codex_app
from codex_widget.codex_app import codex_app_is_running


def test_detects_desktop_chatgpt_process_name() -> None:
    assert codex_app_is_running(['ChatGPT.exe']) is True


def test_keeps_legacy_codex_process_compatibility() -> None:
    assert codex_app_is_running(['Codex.exe']) is True


def test_detects_vscode_or_cli_codex_process() -> None:
    assert codex_app_is_running(['codex.exe']) is True
    assert codex_app_is_running(['CODEX.EXE']) is True


def test_ignores_helper_and_widget_process_names() -> None:
    process_names = ['codex-code-mode-host.exe', 'codex_status_widget.exe',
                     'codex-windows-sandbox-service.exe', 'Code.exe']
    assert codex_app_is_running(process_names) is False


def test_ignores_widget_quota_process_but_detects_other_codex(monkeypatch) -> None:
    monkeypatch.setattr(codex_app.sys, 'platform', 'win32')
    monkeypatch.setattr(codex_app.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(codex_app, 'QUOTA_PROCESS_IDS', {123})
    result = SimpleNamespace(returncode=0, stdout='"codex.exe","123","Console","1","10 K"\n')
    monkeypatch.setattr(codex_app.subprocess, 'run', lambda *args, **kwargs: result)

    assert codex_app_is_running() is False

    result.stdout += '"codex.exe","456","Console","1","10 K"\n'
    assert codex_app_is_running() is True
