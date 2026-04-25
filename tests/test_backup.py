import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import finance.backup as backup


class FixedDateTime:
    _current = datetime(2026, 4, 16, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls._current.astimezone(tz) if cls._current.tzinfo else cls._current.replace(tzinfo=tz)
        return cls._current

    @classmethod
    def utcnow(cls):
        return cls._current

    @classmethod
    def fromisoformat(cls, value):
        return datetime.fromisoformat(value)


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO sample(value) VALUES ('ok')")
    conn.commit()
    conn.close()


def test_manual_backup_prunes_to_retention_limit(tmp_path, monkeypatch):
    db_path = tmp_path / 'finance.db'
    _create_db(db_path)

    backup_root = tmp_path / 'backups'
    monkeypatch.setattr(backup, 'DEFAULT_BACKUP_DIR', str(backup_root))
    monkeypatch.setattr(backup, 'datetime', FixedDateTime)
    monkeypatch.setattr(backup, 'NAS_SYNC_TARGET', '')

    created = []
    for hour in range(11):
        FixedDateTime._current = datetime(2026, 4, 16, 12, 0, 0) + timedelta(hours=hour)
        created.append(Path(backup.backup_db(str(db_path), kind='manual')))

    manual_files = sorted((backup_root / 'manual').glob('finance_manual_*.db'))

    assert len(manual_files) == 10
    assert created[-1] in manual_files
    assert created[0] not in manual_files


def test_backup_status_reports_each_tier_and_due_state(tmp_path, monkeypatch):
    db_path = tmp_path / 'finance.db'
    _create_db(db_path)

    backup_root = tmp_path / 'backups'
    monkeypatch.setattr(backup, 'DEFAULT_BACKUP_DIR', str(backup_root))
    monkeypatch.setattr(backup, 'datetime', FixedDateTime)

    (backup_root / 'hourly').mkdir(parents=True)
    (backup_root / 'daily').mkdir(parents=True)
    (backup_root / 'manual').mkdir(parents=True)

    hourly_file = backup_root / 'hourly' / 'finance_hourly_20260416_090000.db'
    hourly_file.write_bytes(b'hourly')
    daily_file = backup_root / 'daily' / 'finance_daily_20260416_000000.db'
    daily_file.write_bytes(b'daily')
    manual_file = backup_root / 'manual' / 'finance_manual_20260416_113000.db'
    manual_file.write_bytes(b'manual')

    status = backup.get_backup_status(str(db_path))

    assert status['backup_root'] == str(backup_root)
    assert status['hourly']['max_sets'] == 24
    assert status['hourly']['count'] == 1
    assert status['hourly']['status'] == 'due'
    assert status['daily']['status'] == 'ok'
    assert status['weekly']['status'] == 'missing'
    assert status['manual']['count'] == 1
    assert status['manual']['latest_file'].endswith('finance_manual_20260416_113000.db')


def test_resolve_backup_dir_prefers_env_override(tmp_path, monkeypatch):
    env_backup_root = tmp_path / 'env-backups'
    monkeypatch.setenv('FINANCE_BACKUP_DIR', str(env_backup_root))

    resolved = backup._resolve_backup_dir(None)

    assert resolved == env_backup_root


def test_sync_to_nas_uses_latest_backup_available(tmp_path, monkeypatch):
    db_path = tmp_path / 'finance.db'
    _create_db(db_path)

    backup_root = tmp_path / 'backups'
    hourly_dir = backup_root / 'hourly'
    manual_dir = backup_root / 'manual'
    hourly_dir.mkdir(parents=True)
    manual_dir.mkdir(parents=True)

    old_backup = hourly_dir / 'finance_hourly_20260416_090000.db'
    old_backup.write_text('old-backup')
    latest_backup = manual_dir / 'finance_manual_20260416_120000.db'
    latest_backup.write_text('latest-backup')
    target = tmp_path / 'nas' / 'finance_readonly.db'
    target.parent.mkdir(parents=True)

    monkeypatch.setattr(backup, 'DEFAULT_BACKUP_DIR', str(backup_root))
    monkeypatch.setattr(backup, 'NAS_SYNC_TARGET', str(target))
    monkeypatch.setattr(backup, 'datetime', FixedDateTime)

    result = backup.sync_to_nas(str(db_path), force=True)

    assert result['ok'] is True
    assert result['source'].endswith('finance_manual_20260416_120000.db')
    assert target.read_text() == 'latest-backup'


def test_sync_to_nas_uses_configured_ssh_port(tmp_path, monkeypatch):
    db_path = tmp_path / 'finance.db'
    _create_db(db_path)

    backup_root = tmp_path / 'backups'
    manual_dir = backup_root / 'manual'
    manual_dir.mkdir(parents=True)

    latest_backup = manual_dir / 'finance_manual_20260416_120000.db'
    latest_backup.write_text('latest-backup')

    monkeypatch.setattr(backup, 'DEFAULT_BACKUP_DIR', str(backup_root))
    monkeypatch.setattr(backup, 'NAS_SYNC_TARGET', 'chfun@192.168.1.44:/volume1/finance/finance_readonly.db')
    monkeypatch.setattr(backup, 'NAS_SYNC_SSH_PORT', '22')
    monkeypatch.setattr(backup, 'datetime', FixedDateTime)

    called = {}

    def fake_run(cmd, stdin=None, capture_output=None, timeout=None):
        called['cmd'] = cmd
        called['timeout'] = timeout
        return type('R', (), {'returncode': 0, 'stderr': b''})()

    monkeypatch.setattr(backup.subprocess, 'run', fake_run)

    result = backup.sync_to_nas(str(db_path), force=True)

    assert result['ok'] is True
    assert called['cmd'][:5] == [
        'ssh',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', f'UserKnownHostsFile={Path.home() / ".ssh" / "known_hosts"}',
    ]
    assert called['cmd'][5:7] == ['-p', '22']
