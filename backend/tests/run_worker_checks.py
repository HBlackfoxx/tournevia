"""Docker-only runner: use bootstrap credentials for fresh disposable test databases."""
import os
import pytest
from app.database_roles import DatabaseAdminSettings

os.environ['TEST_DATABASE_URL']=DatabaseAdminSettings().admin_url.render_as_string(hide_password=False)
os.environ['RUN_WORKER_TESTS']='1'  # This runner exists for these tests; never pass by skipping them all.
raise SystemExit(pytest.main(['-q','-m','worker','--basetemp','/tmp/vrp-worker-tests','-p','no:cacheprovider','--tb=short']))
