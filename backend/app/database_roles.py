"""Explicit installation roles and grants; only setup receives administrator credentials."""
import psycopg
from psycopg import sql
from pydantic import SecretStr

from app.config import Settings

TABLES = ('organizations', 'depots', 'vehicles', 'orders', 'customers', 'planning_jobs', 'plans',
          'users', 'login_sessions', 'login_throttles', 'alembic_version')
WRITABLE = ('depots', 'vehicles', 'orders', 'customers', 'planning_jobs', 'plans', 'users')
EPHEMERAL = ('login_sessions', 'login_throttles')


class DatabaseAdminSettings(Settings):
    postgres_user: str = 'vrp_owner'
    postgres_password: SecretStr = SecretStr('')
    runtime_db_user: str = 'vrp_app'
    runtime_db_password: SecretStr = SecretStr('')
    migration_db_user: str = 'vrp_migrator'
    migration_db_password: SecretStr = SecretStr('')

    @property
    def admin_url(self):
        return self.database_url.set(username=self.postgres_user, password=self.postgres_password.get_secret_value())

    def migration_settings(self):
        return self.model_copy(update={'db_user': self.migration_db_user, 'db_password': self.migration_db_password})

    def runtime_settings(self):
        return self.model_copy(update={'db_user': self.runtime_db_user, 'db_password': self.runtime_db_password})


def connect(url, *, autocommit=False):
    return psycopg.connect(**url.translate_connect_args(username='user', database='dbname'),
                           connect_timeout=5, autocommit=autocommit)


def prepare_roles(settings: DatabaseAdminSettings):
    names = [settings.postgres_user, settings.runtime_db_user, settings.migration_db_user]
    if len(set(names)) != 3 or any(not n or len(n.encode()) > 63 or n.startswith('pg_') for n in names):
        raise ValueError('Use three distinct non-reserved database role names of at most 63 bytes.')
    passwords = [settings.postgres_password.get_secret_value(), settings.runtime_db_password.get_secret_value(),
                 settings.migration_db_password.get_secret_value()]
    if any(len(p) < 20 for p in passwords[1:]) or len(set(passwords)) != 3:
        raise ValueError('Runtime and migration passwords must be distinct from each other and bootstrap, with at least 20 characters.')
    with connect(settings.admin_url) as connection:
        connection.execute('SELECT pg_advisory_xact_lock(73645104)')
        for name, password, kind in [(names[1], passwords[1], 'runtime'), (names[2], passwords[2], 'migration')]:
            tag = 'france-vrp-managed-' + kind
            row = connection.execute("SELECT oid, shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
            if row:
                if row[1] != tag or connection.execute('SELECT 1 FROM pg_auth_members WHERE roleid=%s OR member=%s', (row[0],row[0])).fetchone():
                    raise ValueError('Refusing to modify an unmanaged role or a role with memberships.')
            else:
                connection.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(name)))
                connection.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(name), sql.Literal(tag)))
            # Send a SCRAM verifier, never a cleartext password in SQL/logging.
            verifier = connection.pgconn.encrypt_password(password.encode(), name.encode(), b'scram-sha-256').decode()
            connection.execute(sql.SQL('ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD {}').format(sql.Identifier(name), sql.Literal(verifier)))
        migration = sql.Identifier(settings.migration_db_user)
        database = sql.Identifier(settings.db_name)
        # Only the known application schema is transferable. Never REASSIGN OWNED
        # across a cluster or silently adopt unrelated tables/extensions.
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
        if any(t[0] not in TABLES for t in tables):
            raise ValueError('Unexpected public tables; review this database before changing its owner.')
        connection.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(database, migration))
        connection.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(database))
        connection.execute(sql.SQL('ALTER SCHEMA public OWNER TO {}').format(migration))
        connection.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        for (table,) in tables:
            connection.execute(sql.SQL('ALTER TABLE public.{} OWNER TO {}').format(sql.Identifier(table), migration))


def grant_statements(database: str, runtime: str):
    role = sql.Identifier(runtime)
    yield sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC, {}').format(sql.Identifier(database), role)
    yield sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database), role)
    yield sql.SQL('REVOKE ALL ON SCHEMA public FROM PUBLIC, {}').format(role)
    yield sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(role)
    for table in TABLES:
        identifier = sql.Identifier('public', table)
        yield sql.SQL('REVOKE ALL ON TABLE {} FROM PUBLIC, {}').format(identifier, role)
        yield sql.SQL('GRANT SELECT ON TABLE {} TO {}').format(identifier, role)
    # SELECT FOR UPDATE of the company lock requires UPDATE on at least one column; address lookup,
    # delivery numbering and the safety margin are the company settings the runtime role changes.
    yield sql.SQL('GRANT INSERT, UPDATE (id, public_address_lookup, order_number_format, order_number_start, '
                  'safety_margin_minutes) ON public.organizations TO {}').format(role)
    for table in WRITABLE + EPHEMERAL:
        yield sql.SQL('GRANT INSERT, UPDATE ON TABLE public.{} TO {}').format(sql.Identifier(table), role)
    for table in EPHEMERAL:
        yield sql.SQL('GRANT DELETE ON TABLE public.{} TO {}').format(sql.Identifier(table), role)
    # The runtime role's only ways to delete source records: removing a company's examples (migration 0016),
    yield sql.SQL('REVOKE ALL ON FUNCTION public.remove_examples(uuid) FROM PUBLIC')
    yield sql.SQL('GRANT EXECUTE ON FUNCTION public.remove_examples(uuid) TO {}').format(role)
    # deleting chosen deliveries of one company that no waiting calculation uses (migration 0014).
    yield sql.SQL('REVOKE ALL ON FUNCTION public.delete_orders(uuid, uuid[]) FROM PUBLIC')
    yield sql.SQL('GRANT EXECUTE ON FUNCTION public.delete_orders(uuid, uuid[]) TO {}').format(role)
    # And deleting a depot or place that is not the main depot and that nothing names (migration 0015).
    yield sql.SQL('REVOKE ALL ON FUNCTION public.delete_depot(uuid, uuid) FROM PUBLIC')
    yield sql.SQL('GRANT EXECUTE ON FUNCTION public.delete_depot(uuid, uuid) TO {}').format(role)


def apply_grants(settings: DatabaseAdminSettings):
    with connect(settings.admin_url) as connection:
        for statement in grant_statements(settings.db_name, settings.runtime_db_user):
            connection.execute(statement)
