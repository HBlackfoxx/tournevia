"""Run backend tests including PostgreSQL using the configured bootstrap administrator.

The role must have CREATEDB. Tests create/drop only fresh vrp_test_<random> databases.
The configured database and its records are not reset or deleted.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.database_roles import DatabaseAdminSettings


if __name__ == '__main__':
    environment = os.environ.copy()
    environment['TEST_DATABASE_URL'] = DatabaseAdminSettings().admin_url.render_as_string(hide_password=False)
    raise SystemExit(subprocess.call([
        sys.executable, '-m', 'pytest', '-c', 'backend/pytest.ini', 'backend/tests', 'scripts/tests',
        '--basetemp', '.pytest-tmp', '-q', *sys.argv[1:],
    ], cwd=ROOT, env=environment))
