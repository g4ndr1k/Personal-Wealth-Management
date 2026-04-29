import logging

from agent.app import imap_source
from agent.app.imap_source import IMAPPoller


def _acct(path, **overrides):
    data = {
        "id": "gmail_user",
        "name": "user",
        "email": "user@gmail.com",
        "host": "imap.gmail.com",
        "port": 993,
        "folders": ["INBOX"],
        "auth_source": "keychain",
        "keychain_service": "agentic-ai-mail-imap",
        "secrets_file": str(path),
    }
    data.update(overrides)
    return data


def test_keychain_miss_file_fallback_success_normalizes_whitespace(
    tmp_path, monkeypatch
):
    secret_file = tmp_path / "imap.toml"
    secret_file.write_text(
        '[[accounts]]\n'
        'email = "user@gmail.com"\n'
        'app_password = "abcd efgh\\tijkl\\nmnop"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(imap_source, "_keychain_get", lambda service, account: None)

    assert imap_source._load_app_password(_acct(secret_file)) == "abcdefghijklmnop"


def test_keychain_miss_file_fallback_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(imap_source, "_keychain_get", lambda service, account: None)

    assert imap_source._load_app_password(_acct(tmp_path / "missing.toml")) is None
    status = imap_source.credential_debug_status(_acct(tmp_path / "missing.toml"))
    assert status["configured_source"] == "missing"
    assert status["credential_present"] is False


def test_credential_status_reports_presence_without_secret(tmp_path, monkeypatch):
    secret = "abcd efgh ijkl mnop"
    secret_file = tmp_path / "imap.toml"
    secret_file.write_text(
        '[[accounts]]\n'
        'email = "user@gmail.com"\n'
        f'app_password = "{secret}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(imap_source, "_keychain_get", lambda service, account: None)

    status = imap_source.credential_debug_status(_acct(secret_file))

    assert status["credential_present"] is True
    assert status["file_present"] is True
    assert "app_password" not in status
    assert secret not in repr(status)


def test_startup_logs_source_without_secret(tmp_path, monkeypatch, caplog):
    secret = "abcd efgh ijkl mnop"
    secret_file = tmp_path / "imap.toml"
    secret_file.write_text(
        '[[accounts]]\n'
        'email = "user@gmail.com"\n'
        f'app_password = "{secret}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(imap_source, "_keychain_get", lambda service, account: None)

    caplog.set_level(logging.INFO, logger="agent.imap_source")
    IMAPPoller(_acct(secret_file), state=None, imap_cfg={})

    assert "IMAP credential source for user@gmail.com" in caplog.text
    assert "credential_present=True" in caplog.text
    assert secret not in caplog.text
    assert "abcdefghijklmnop" not in caplog.text
