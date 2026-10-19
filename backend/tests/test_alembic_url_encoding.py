"""Alembic must accept URL-encoded passwords and Unix-socket paths."""
import os
from pathlib import Path
import subprocess
import sys


def test_offline_migrations_accept_percent_encoded_url():
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "APP_ENV": "development", "AUTO_CREATE_SCHEMA": "false",
             "DATABASE_URL": "postgresql+psycopg://test:percent%25encoded@/test?host=%2Ftmp"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, "Offline Alembic failed for a percent-encoded URL"
    assert "FORCE ROW LEVEL SECURITY" in result.stdout
