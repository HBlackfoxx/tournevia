import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database_backup
from app.database_roles import DatabaseAdminSettings
from database_backup import BackupError, ComposeDatabase, prune, run_age, workspace_database


def test_workspace_selects_its_database_and_project(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_NAME', 'unrelated_database')
    (tmp_path/'workspace.json').write_text('{"project":"tournevia-server-test"}')
    (tmp_path/'workspace.env').write_text('\n'.join([
        'POSTGRES_DB=selected_database', 'POSTGRES_USER=owner', 'POSTGRES_PASSWORD=owner-test',
        'MIGRATION_DB_USER=migrator', 'MIGRATION_DB_PASSWORD=migrator-test',
        'RUNTIME_DB_USER=app', 'RUNTIME_DB_PASSWORD=app-test']))
    database = workspace_database(tmp_path)
    assert database.settings.db_name == 'selected_database'
    assert database.settings.migration_db_user == 'migrator'
    assert database.prefix[database.prefix.index('-p') + 1] == 'tournevia-server-test'
    assert str(tmp_path/'workspace.env') in database.prefix
    assert 'owner-test' not in database.prefix


def test_workspace_rejects_invalid_project(tmp_path):
    (tmp_path/'workspace.json').write_text('{"project":"../other"}')
    with pytest.raises(BackupError):
        workspace_database(tmp_path)


FAKE_AGE = """import sys
args = sys.argv[1:]
source, out = args[-1], args[args.index('-o') + 1]
data = open(source, 'rb').read()
open(out, 'wb').write(b'AGE:' + data if '-r' in args else data.removeprefix(b'AGE:'))
"""


@pytest.fixture
def fake_age(tmp_path, monkeypatch):
    script = tmp_path / 'fake_age.py'
    script.write_text(FAKE_AGE)
    monkeypatch.setattr(database_backup, 'AGE', [sys.executable, str(script)])


def test_encrypted_backup_publishes_only_ciphertext_of_the_verified_archive(tmp_path, fake_age):
    database = ComposeDatabase(DatabaseAdminSettings(_env_file=None, postgres_password='owner-test', migration_db_password='migrator-test'))
    database.check_schema = lambda name: None
    database.run = lambda tool, args, output_file=None, **_: output_file.write(b'PGDMP-archive')
    folder = tmp_path / 'backups'
    path = database.backup(folder / 'vrp-20260923T120000Z-abcdef12.dump.age', 'age1example')
    assert path.read_bytes() == b'AGE:PGDMP-archive'
    assert [f.name for f in folder.iterdir()] == [path.name]  # No plaintext partial file remains.
    plain = tmp_path / 'plain.dump'
    run_age(['-d', '-i', 'identity.txt'], path, plain)
    assert plain.read_bytes() == b'PGDMP-archive'


def test_a_non_archive_is_never_encrypted_or_published(tmp_path, fake_age):
    database = ComposeDatabase(DatabaseAdminSettings(_env_file=None, postgres_password='owner-test', migration_db_password='migrator-test'))
    database.check_schema = lambda name: None
    database.run = lambda tool, args, output_file=None, **_: output_file.write(b'not an archive')
    with pytest.raises(BackupError):
        database.backup(tmp_path / 'vrp-20260923T120000Z-abcdef12.dump.age', 'age1example')
    assert list(tmp_path.iterdir()) == [tmp_path / 'fake_age.py']


def test_prune_keeps_the_newest_archives_and_never_touches_other_files(tmp_path):
    names = ['vrp-20260101T000000Z-aaaaaaaa.dump', 'vrp-20260102T000000Z-bbbbbbbb.dump.age',
             'vrp-20260103T000000Z-cccccccc.dump', 'notes.txt', 'vrp-manual-copy.dump']
    for name in names:
        (tmp_path / name).write_text('x')
    removed = prune(tmp_path, 2)
    assert [f.name for f in removed] == ['vrp-20260101T000000Z-aaaaaaaa.dump']
    assert sorted(f.name for f in tmp_path.iterdir()) == sorted(names[1:])
    with pytest.raises(BackupError):
        prune(tmp_path, 0)


class FakeDatabase:
    def __init__(self, databases=('vrp', 'vrp_restore_' + 'a' * 32), schema_ok=True):
        self.databases, self.schema_ok = set(databases), schema_ok

    def query(self, statement, **_):
        return '1' if any(f"'{name}'" in statement for name in self.databases) else ''

    def check_schema(self, name):
        if not self.schema_ok:
            raise BackupError('Database schema does not match this application.')


RESTORED = 'vrp_restore_' + 'a' * 32


@pytest.fixture
def installation(tmp_path, monkeypatch):
    env_file = tmp_path / '.env'
    env_file.write_bytes(b'POSTGRES_USER=owner\r\nPOSTGRES_DB=vrp\r\nDB_NAME=vrp\r\nAPP_MODE=local\r\n')
    calls = []

    def compose_run(compose, *args):
        calls.append((args, database_backup.env_database(env_file)))
        return 'postgres\nsetup\napi\nworker\nhttps\n' if args[0] == 'config' else ''
    monkeypatch.setattr(database_backup, 'compose_run', compose_run)
    return env_file, calls


def test_env_file_names_one_database_and_switches_atomically(tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_bytes(b'POSTGRES_DB=vrp\r\nDB_NAME=vrp\r\nOTHER=POSTGRES_DB=x\r\n')
    assert database_backup.env_database(env_file) == 'vrp'
    assert database_backup.env_database(env_file, RESTORED) == 'vrp'
    assert env_file.read_bytes() == f'POSTGRES_DB={RESTORED}\r\nDB_NAME={RESTORED}\r\nOTHER=POSTGRES_DB=x\r\n'.encode()
    assert [f.name for f in tmp_path.iterdir()] == ['.env']
    for text in ('DB_NAME=vrp\n', 'POSTGRES_DB=vrp\nPOSTGRES_DB=vrp\n', 'POSTGRES_DB=vrp\nDB_NAME=other\n'):
        env_file.write_text(text)
        with pytest.raises(BackupError):
            database_backup.env_database(env_file, RESTORED)
        assert env_file.read_text() == text


def test_compose_never_takes_the_database_from_the_caller_environment(monkeypatch):
    seen = {}
    monkeypatch.setenv('POSTGRES_DB', 'vrp')
    monkeypatch.setenv('DB_NAME', 'vrp')
    monkeypatch.setattr(database_backup.subprocess, 'run',
                        lambda args, env, **_: seen.update(env) or database_backup.subprocess.CompletedProcess(args, 0, '', ''))
    database_backup.compose_run(['docker', 'compose'], 'up')
    assert 'POSTGRES_DB' not in seen and 'DB_NAME' not in seen and 'PATH' in seen


def test_cutover_stops_writers_switches_then_starts_on_the_restored_copy(installation):
    env_file, calls = installation
    assert database_backup.cutover(FakeDatabase(), ['docker', 'compose'], env_file, RESTORED) == 'vrp'
    assert calls == [(('stop', 'api', 'worker'), 'vrp'),
                     (('up', '-d', '--no-build', '--wait', '--wait-timeout', '180'), RESTORED),
                     (('config', '--services'), RESTORED), (('restart', 'https'), RESTORED)]
    assert database_backup.env_database(env_file) == RESTORED


def test_start_restarts_the_proxy_behind_an_operator_proxy_too(monkeypatch):
    calls = []
    monkeypatch.setattr(database_backup, 'compose_run', lambda compose, *args: calls.append(args) or (
        'postgres\nsetup\napi\nworker\nvalhalla\nedge\n' if args[0] == 'config' else ''))
    database_backup.start(['docker', 'compose'])
    assert calls[1:] == [('config', '--services'), ('restart', 'edge')]


def test_failed_cutover_starts_the_previous_database_again(installation, monkeypatch):
    env_file, calls = installation
    recorded = database_backup.compose_run

    def first_start_fails(compose, *args):
        result = recorded(compose, *args)
        if args[0] == 'up' and len([c for c in calls if c[0][0] == 'up']) == 1:
            raise BackupError('docker compose up failed (exit 1).')
        return result
    monkeypatch.setattr(database_backup, 'compose_run', first_start_fails)
    with pytest.raises(BackupError, match='running on vrp again'):
        database_backup.cutover(FakeDatabase(), ['docker', 'compose'], env_file, RESTORED)
    assert [(c[0][0], c[1]) for c in calls] == [('stop', 'vrp'), ('up', RESTORED), ('up', 'vrp'), ('config', 'vrp'), ('restart', 'vrp')]
    assert database_backup.env_database(env_file) == 'vrp'


@pytest.mark.parametrize('target, database', [
    ('postgres', FakeDatabase()), ('Bad-Name', FakeDatabase()), ('vrp_restore_' + 'b' * 32, FakeDatabase()),
    (RESTORED, FakeDatabase(schema_ok=False)), ('vrp', FakeDatabase())])
def test_cutover_refuses_before_touching_services(installation, target, database):
    env_file, calls = installation
    before = env_file.read_bytes()
    with pytest.raises(BackupError):
        database_backup.cutover(database, ['docker', 'compose'], env_file, target)
    assert calls == [] and env_file.read_bytes() == before
