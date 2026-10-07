from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QWidget

from codex_widget.reset_credits import RESET_CREDITS_TIMEOUT_MS, RESET_CREDITS_URL
from codex_widget.config import AppConfig
from codex_widget.ui import CodexWidget


class _FakeSignal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:  # noqa: ANN001
        self.callback = callback


class _FakeReply:
    def __init__(self, body: bytes = b'', status_code: int | None = 200) -> None:
        self.finished = _FakeSignal()
        self.body = body
        self.status_code = status_code
        self.deleted = False

    def attribute(self, attribute) -> int | None:  # noqa: ANN001
        assert attribute == QNetworkRequest.Attribute.HttpStatusCodeAttribute
        return self.status_code

    def error(self) -> QNetworkReply.NetworkError:
        return QNetworkReply.NetworkError.NoError

    def readAll(self) -> QByteArray:
        return QByteArray(self.body)

    def deleteLater(self) -> None:
        self.deleted = True


class _FakeNetworkManager:
    def __init__(self, reply: _FakeReply) -> None:
        self.reply = reply
        self.request = None

    def get(self, request):  # noqa: ANN001
        self.request = request
        return self.reply


def test_menu_contains_reset_credit_query_action() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    triggered = []
    widget.refresh = lambda: None
    widget.query_reset_credits = lambda: triggered.append(True)
    widget.mark_idle = lambda: None
    widget._lock_text = lambda: '锁定位置'
    widget.toggle_lock = lambda: None
    widget.install_hook_to_codex = lambda: None
    widget.open_sessions_dir = lambda: None
    widget.open_state_dir = lambda: None
    widget.config = AppConfig.default()
    widget.toggle_screen_output = lambda enabled: None

    CodexWidget._build_menu(widget)
    matches = [action for action in widget.menu.actions() if action.text() == '查询重置额度']

    assert len(matches) == 1
    matches[0].trigger()
    assert triggered == [True]
    widget.close()
    assert app is not None


def test_query_sends_bearer_get_without_other_credentials(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    codex_home = tmp_path / '.codex'
    sessions_dir = codex_home / 'sessions'
    codex_home.mkdir()
    (codex_home / 'auth.json').write_text(
        json.dumps(
            {
                'tokens': {
                    'access_token': 'access-secret',
                    'refresh_token': 'refresh-secret',
                    'account_id': 'account-secret',
                }
            }
        ),
        encoding='utf-8',
    )
    reply = _FakeReply()
    manager = _FakeNetworkManager(reply)
    widget = QWidget()
    widget.config = SimpleNamespace(codex=SimpleNamespace(sessions_dir=sessions_dir))
    widget.reset_credits_action = QAction(widget)
    widget._network_manager = manager
    widget._reset_credits_reply = None
    critical_messages = []
    monkeypatch.setattr(
        'codex_widget.ui.QMessageBox.critical',
        lambda *args: critical_messages.append(args),
    )

    CodexWidget.query_reset_credits(widget)

    request = manager.request
    assert request is not None
    assert request.url().toString() == RESET_CREDITS_URL
    assert bytes(request.rawHeader('Authorization')) == b'Bearer access-secret'
    assert bytes(request.rawHeader('Accept')) == b'application/json'
    assert bytes(request.rawHeader('Cookie')) == b''
    assert request.transferTimeout() == RESET_CREDITS_TIMEOUT_MS
    header_values = b'\n'.join(bytes(request.rawHeader(bytes(name).decode())) for name in request.rawHeaderList())
    assert b'refresh-secret' not in header_values
    assert b'account-secret' not in header_values
    assert widget.reset_credits_action.isEnabled() is False
    assert reply.finished.callback is not None
    assert critical_messages == []
    widget.close()
    assert app is not None


def test_successful_query_shows_only_formatted_message(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    body = json.dumps(
        {
            'available_count': 1,
            'credits': [
                {
                    'id': 'credit-secret',
                    'status': 'available',
                    'title': 'Full reset',
                    'granted_at': '2026-07-25T16:30:00Z',
                    'expires_at': '2026-08-24T16:30:00Z',
                }
            ],
        }
    ).encode()
    reply = _FakeReply(body)
    widget = QWidget()
    widget.reset_credits_action = QAction(widget)
    widget.reset_credits_action.setEnabled(False)
    widget._reset_credits_reply = reply
    information_messages = []
    critical_messages = []
    monkeypatch.setattr(
        'codex_widget.ui.QMessageBox.information',
        lambda *args: information_messages.append(args),
    )
    monkeypatch.setattr(
        'codex_widget.ui.QMessageBox.critical',
        lambda *args: critical_messages.append(args),
    )

    CodexWidget._finish_reset_credits_query(widget, reply)

    assert len(information_messages) == 1
    assert information_messages[0][1] == '重置额度'
    assert 'Full reset' in information_messages[0][2]
    assert 'credit-secret' not in information_messages[0][2]
    assert critical_messages == []
    assert widget.reset_credits_action.isEnabled() is True
    assert widget._reset_credits_reply is None
    assert reply.deleted is True
    widget.close()
    assert app is not None


def test_unauthorized_query_shows_safe_credential_error(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    reply = _FakeReply(b'{"token": "server-secret"}', status_code=401)
    widget = QWidget()
    widget.reset_credits_action = QAction(widget)
    widget.reset_credits_action.setEnabled(False)
    widget._reset_credits_reply = reply
    information_messages = []
    critical_messages = []
    monkeypatch.setattr(
        'codex_widget.ui.QMessageBox.information',
        lambda *args: information_messages.append(args),
    )
    monkeypatch.setattr(
        'codex_widget.ui.QMessageBox.critical',
        lambda *args: critical_messages.append(args),
    )

    CodexWidget._finish_reset_credits_query(widget, reply)

    assert information_messages == []
    assert len(critical_messages) == 1
    assert '凭证已失效' in critical_messages[0][2]
    assert 'Authorization' in critical_messages[0][2]
    assert 'server-secret' not in critical_messages[0][2]
    assert widget.reset_credits_action.isEnabled() is True
    assert reply.deleted is True
    widget.close()
    assert app is not None
