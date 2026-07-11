from __future__ import annotations

from codex_widget.codex_app import codex_app_is_running


def test_detects_desktop_chatgpt_process_name() -> None:
    assert codex_app_is_running(['ChatGPT.exe']) is True


def test_keeps_legacy_codex_process_compatibility() -> None:
    assert codex_app_is_running(['Codex.exe']) is True


def test_ignores_cli_and_widget_process_names() -> None:
    process_names = ['codex.exe', 'codex-code-mode-host.exe', 'codex_status_widget.exe']

    assert codex_app_is_running(process_names) is False
