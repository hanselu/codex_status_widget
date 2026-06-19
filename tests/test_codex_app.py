from __future__ import annotations

from codex_widget.codex_app import codex_app_is_running


def test_detects_desktop_codex_process_name() -> None:
    assert codex_app_is_running(['Codex.exe']) is True


def test_ignores_cli_and_widget_process_names() -> None:
    assert codex_app_is_running(['codex.exe', 'codex_status_widget.exe']) is False
