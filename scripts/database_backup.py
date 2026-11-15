"""Compose database backup, isolated restore and cutover. Never overwrite a database; prune only on request.

Archives can be encrypted with age (https://age-encryption.org) for off-host copies, and old
archives this script created can be pruned explicitly with `prune --keep N`. `cutover` switches
the installation to a validated restored copy and keeps the previous database unchanged.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from psycopg import sql
from app.database_roles import DatabaseAdminSettings, TABLES, grant_statements
from app.main import SCHEMA_REVISION


class BackupError(Exception):
    pass


# The age executable; tests substitute a stand-in. Encryption needs only a public recipient.
AGE = ['age']
ARCHIVE_NAME = re.compile(r'vrp-\d{8}T\d{6}Z-[0-9a-f]{8}\.dump(\.age)?')


def run_age(args: list[str], source: Path, destination: Path) -> None:
    try:
        result = subprocess.run([*AGE, *args, '-o', str(destination), str(source)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=360, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackupError(f'age did not complete ({type(error).__name__}); install age or omit encryption.') from None
    if result.returncode:
        raise BackupError(f'age failed (exit {result.returncode}); no archive was published.')


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` archives that this script named; never touch other files."""
    if keep < 1:
        raise BackupError('Keep at least one backup.')
    archives = sorted((f for f in directory.iterdir() if f.is_file() and ARCHIVE_NAME.fullmatch(f.name)),
                      key=lambda f: f.name, reverse=True)
    removed = archives[keep:]
    for archive in removed:
        archive.unlink()
    return removed


DATABASE_NAME = re.compile(r'[a-z_][a-z0-9_]{0,62}')


def compose_run(compose: list[str], *args: str) -> str:
    # Compose prefers process variables over --env-file values; only the env file names the database.
    env = {key: value for key, value in os.environ.items() if key not in ('POSTGRES_DB', 'DB_NAME')}
    try:
        result = subprocess.run([*compose, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackupError(f'docker compose {args[0]} did not complete ({type(error).__name__}).') from None
    if result.returncode:
        # Compose output can include configuration values; it is not echoed.
        raise BackupError(f'docker compose {args[0]} failed (exit {result.returncode}); see docker compose ps and logs.')
    return result.stdout


def env_database(env_file: Path, replacement: str | None = None) -> str:
    """Return the database the env file names; with `replacement`, atomically point it there instead.

    POSTGRES_DB names the database for every service; a host-side DB_NAME, when present, follows it.
    """
    lines = env_file.read_bytes().decode('utf-8').splitlines(keepends=True)
    found = {key: [i for i, line in enumerate(lines) if line.startswith(key + '=')] for key in ('POSTGRES_DB', 'DB_NAME')}
    if len(found['POSTGRES_DB']) != 1 or len(found['DB_NAME']) > 1:
        raise BackupError('The env file must set POSTGRES_DB exactly once and DB_NAME at most once.')
    current = lines[found['POSTGRES_DB'][0]].split('=', 1)[1].strip()
    if any(lines[i].split('=', 1)[1].strip() != current for i in found['DB_NAME']):
        raise BackupError('DB_NAME differs from POSTGRES_DB in the env file; align them before a cutover.')
    if replacement is None:
        return current
    for key, indexes in found.items():
        for i in indexes:
            lines[i] = f'{key}={replacement}' + ('\r\n' if lines[i].endswith('\r\n') else '\n')
    partial = env_file.with_name(env_file.name + '.' + uuid4().hex + '.partial')
    try:
        with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8', newline='') as output:
            output.write(''.join(lines))
            output.flush(); os.fsync(output.fileno())
        os.replace(partial, env_file)
    finally:
        if partial.exists():
            partial.unlink()
    return current


def start(compose: list[str]) -> None:
    compose_run(compose, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '180')
    # The bundled proxy (https, or edge behind an operator's proxy) resolves the API address once, when it starts.
    services = compose_run(compose, 'config', '--services').split()
    for proxy in ('https', 'edge'):
        if proxy in services:
            compose_run(compose, 'restart', proxy)


def cutover(database: 'ComposeDatabase', compose: list[str], env_file: Path, target: str) -> str:
    """Switch the installation to `target` (normally a validated restored copy); return the previous name.

    The API and worker stop first, so neither database is written during the switch; setup then
    reapplies migrations and grants to the target. If the installation does not become healthy on
    the target, the previous name is put back and started again. No database is changed or removed.
    """
    if not DATABASE_NAME.fullmatch(target) or target in ('postgres', 'template0', 'template1'):
        raise BackupError('Name an existing application database.')
    exists = database.query(sql.SQL('SELECT 1 FROM pg_database WHERE datname={};').format(sql.Literal(target)).as_string(), database='postgres', admin=True)
    if not exists:
        raise BackupError('Cutover target does not exist.')
    database.check_schema(target)
    previous = env_database(env_file)
    if previous == target:
        raise BackupError('The installation already uses that database.')
    compose_run(compose, 'stop', 'api', 'worker')
    env_database(env_file, target)
    try:
        start(compose)
    except BackupError:
        env_database(env_file, previous)
        try:
            start(compose)
        except BackupError:
            raise BackupError(f'Cutover to {target} failed and the installation did not restart on {previous}; '
                              'check docker compose ps and logs.') from None
        raise BackupError(f'Cutover to {target} failed; the installation is running on {previous} again.') from None
    return previous


def switched(target: str, previous: str, back: str) -> str:
    return (f'Now running on {target}. The previous database {previous} is unchanged; keep it until named sign-in, '
            f'saved plans and a new calculation have been checked.\nTo switch back: {back}')


class ComposeDatabase:
    def __init__(self, settings: DatabaseAdminSettings, env_file: Path = ROOT / '.env', project: str | None = None):
        self.settings = settings
        self.prefix = ['docker', 'compose', '--env-file', str(env_file), *(['-p', project] if project else []), '-f',
                       str(ROOT / 'deploy/docker-compose.example.yml'), 'exec', '-T',
                       '-e', 'PGUSER', '-e', 'PGPASSWORD', '-e', 'PGDATABASE', '-e', 'PGOPTIONS', 'postgres']

    def run(self, tool, args, *, database=None, admin=False, input_file=None, output_file=None, content=None):
        s = self.settings
        user = s.postgres_user if admin else s.migration_db_user
        password = s.postgres_password if admin else s.migration_db_password
        if not password.get_secret_value():
            raise BackupError('Required database credential is missing.')
        # Values travel in the child environment, never shell text or command arguments.
        env = {**os.environ, 'PGUSER': user, 'PGPASSWORD': password.get_secret_value(),
               'PGDATABASE': database or s.db_name, 'PGOPTIONS': '-c statement_timeout=300000'}
        try:
            result = subprocess.run([*self.prefix, tool, '--host=127.0.0.1', '--no-password', *args],
                env=env, stdin=input_file, input=content, stdout=output_file or subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=360, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BackupError(f'{tool} did not complete ({type(error).__name__}).') from None
        if result.returncode or result.stderr:
            # PostgreSQL errors can echo row values; never print raw stderr or dumps.
            raise BackupError(f'{tool} failed or reported warnings (exit {result.returncode}); output was not accepted.')
        return result.stdout

    def query(self, statement, *, database=None, admin=False):
        return self.run('psql', ['--no-psqlrc', '--set=ON_ERROR_STOP=1', '--tuples-only', '--no-align'],
                        database=database, admin=admin, content=(statement + '\n').encode()).decode().strip()

    def check_schema(self, database):
        version = self.query('SELECT version_num FROM public.alembic_version;', database=database)
        tables = self.query("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;", database=database)
        if version != SCHEMA_REVISION or set(tables.splitlines()) != set(TABLES):
            raise BackupError('Database schema does not match this application; use the matching application version.')

    def backup(self, destination: Path, encrypt_to: str | None = None) -> Path:
        self.check_schema(self.settings.db_name)
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise BackupError('Backup destination exists; choose a new filename.')
        temporary = destination.with_name(destination.name + '.' + uuid4().hex + '.partial')
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                self.run('pg_dump', ['--format=custom', '--no-acl', '--exclude-table-data=public.login_sessions'], output_file=output)
                output.flush(); os.fsync(output.fileno())
            with temporary.open('rb') as source:
                if source.read(5) != b'PGDMP':
                    raise BackupError('Backup did not produce a PostgreSQL custom archive.')
            published = temporary
            if encrypt_to:
                # Encrypt the verified archive; the plaintext partial file is removed below.
                published = temporary.with_name(temporary.name + '.age')
                run_age(['-r', encrypt_to], temporary, published)
            # Atomic, no-overwrite publication on NTFS and POSIX filesystems.
            os.link(published, destination)
        finally:
            for partial in (temporary, temporary.with_name(temporary.name + '.age')):
                if partial.exists():
                    partial.unlink()  # Only this operation's newly-created partial files.
        return destination

    def restore(self, archive: Path, target: str | None = None, identity: Path | None = None) -> str:
        archive = archive.resolve(strict=True)
        if archive.suffix == '.age':
            if identity is None:
                raise BackupError('Encrypted archive: pass --identity with the age identity file.')
            plain = archive.with_name(archive.name + '.' + uuid4().hex + '.partial')
            try:
                run_age(['-d', '-i', str(identity)], archive, plain)
                os.chmod(plain, 0o600)
                return self.restore(plain, target)
            finally:
                if plain.exists():
                    plain.unlink()
        target = target or 'vrp_restore_' + uuid4().hex
        if not re.fullmatch(r'vrp_restore_[0-9a-f]{32}', target) or target == self.settings.db_name:
            raise BackupError('Restore requires a fresh vrp_restore_<32 hex digits> database, never the source.')
        with archive.open('rb') as source:
            self.run('pg_restore', ['--list'], input_file=source)
        exists = self.query(sql.SQL('SELECT 1 FROM pg_database WHERE datname={};').format(sql.Literal(target)).as_string(), database='postgres', admin=True)
        if exists:
            raise BackupError('Restore target already exists; it will not be overwritten.')
        self.query(sql.SQL('CREATE DATABASE {} OWNER {};').format(sql.Identifier(target), sql.Identifier(self.settings.migration_db_user)).as_string(), database='postgres', admin=True)
        self.query(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC;').format(sql.Identifier(target)).as_string(), database='postgres', admin=True)
        try:
            # No --clean or --create: pg_restore only sees the newly-created target,
            # runs without superuser rights, and commits the restore atomically.
            with archive.open('rb') as source:
                self.run('pg_restore', ['--dbname='+target, '--no-owner', '--no-acl', '--single-transaction', '--exit-on-error'],
                         database=target, input_file=source)
            self.check_schema(target)
            # Also clear sessions if restoring a trusted older archive that contained them.
            self.query('DELETE FROM public.login_sessions;', database=target)
            statements = ['BEGIN;'] + [s.as_string()+';' for s in grant_statements(target, self.settings.runtime_db_user)] + ['COMMIT;']
            self.query('\n'.join(statements), database=target, admin=True)
        except Exception as error:
            raise BackupError(f'Restore incomplete in isolated database {target} ({type(error).__name__}); source unchanged. Inspect the isolated target before any use.') from None
        return target

    def summary(self, database):
        fields = sql.SQL(',').join(sql.SQL('{}::text, (SELECT count(*) FROM public.{})').format(sql.Literal(t), sql.Identifier(t)) for t in TABLES if t != 'alembic_version')
        return json.loads(self.query(sql.SQL('SELECT json_build_object({});').format(fields).as_string(), database=database))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, help='Private directory created by start_local.py or start_server.py.')
    sub = parser.add_subparsers(dest='action', required=True)
    save = sub.add_parser('backup'); save.add_argument('--output', type=Path)
    save.add_argument('--encrypt-to', metavar='AGE_RECIPIENT', help='Encrypt the archive for this age public key.')
    restore = sub.add_parser('restore'); restore.add_argument('archive', type=Path)
    restore.add_argument('--identity', type=Path, help='age identity file for an encrypted (.age) archive.')
    switch = sub.add_parser('cutover', help='Switch this installation to a validated restored database and restart it.')
    switch.add_argument('database')
    switch.add_argument('--overlay', type=Path, action='append', default=[],
                        help='Additional Compose file this installation runs with (repeat in order), e.g. deploy/docker-compose.routing.yml.')
    trim = sub.add_parser('prune', help='Delete all but the newest archives this script created in a directory.')
    trim.add_argument('--keep', type=int, required=True); trim.add_argument('--directory', type=Path, default=ROOT / 'backups')
    args = parser.parse_args()
    try:
        if args.action == 'prune':
            removed = prune(args.directory, args.keep)
            print(f'Pruned {len(removed)} older backup(s); kept the newest {args.keep}.')
            return
        if args.action == 'cutover' and args.workspace:
            raise BackupError('Switch a workspace with its launcher: start_local.py or start_server.py --cutover-to DATABASE.')
        database = workspace_database(args.workspace) if args.workspace else ComposeDatabase(DatabaseAdminSettings())
        if args.action == 'cutover':
            env_file = ROOT / '.env'
            files = [ROOT / 'deploy/docker-compose.example.yml', *args.overlay]
            compose = ['docker', 'compose', '--env-file', str(env_file), *[part for f in files for part in ('-f', str(f))]]
            previous = cutover(database, compose, env_file, args.database)
            print(switched(args.database, previous, 'python scripts/database_backup.py cutover ' + previous
                           + ''.join(f' --overlay {f}' for f in args.overlay)))
        elif args.action == 'backup':
            name = 'vrp-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8] + '.dump'
            path = args.output or ROOT / 'backups' / (name + ('.age' if args.encrypt_to else ''))
            print(f'Backup saved: {database.backup(path, args.encrypt_to)}')
        else:
            target = database.restore(args.archive, identity=args.identity)
            print(f'Restored into isolated database: {target}')
            print(json.dumps(database.summary(target), sort_keys=True))
            print('No live configuration changed and no worker started. Validate this copy before a separately planned cutover.')
    except (BackupError, OSError) as error:
        # OSError filenames may be okay but arbitrary library errors are not echoed.
        raise SystemExit(str(error) if isinstance(error, BackupError) else 'Local backup file operation failed; check path and permissions.') from None


def workspace_database(directory: Path) -> ComposeDatabase:
    directory = directory.resolve()
    state = json.loads((directory / 'workspace.json').read_text(encoding='utf-8'))
    project = state.get('project', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]+', project):
        raise BackupError('Invalid workspace project name.')
    env_file = directory / 'workspace.env'
    values = dict(line.split('=', 1) for line in env_file.read_text(encoding='utf-8').splitlines() if line and not line.startswith('#'))
    # The selected workspace, never unrelated caller DB settings, owns the target.
    settings = DatabaseAdminSettings(_env_file=None, db_name=values['POSTGRES_DB'],
        postgres_user=values['POSTGRES_USER'], postgres_password=values['POSTGRES_PASSWORD'],
        migration_db_user=values['MIGRATION_DB_USER'], migration_db_password=values['MIGRATION_DB_PASSWORD'],
        runtime_db_user=values['RUNTIME_DB_USER'], runtime_db_password=values['RUNTIME_DB_PASSWORD'])
    return ComposeDatabase(settings, env_file, project)


if __name__ == '__main__':
    main()
