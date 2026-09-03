import os
import sys
import tempfile
import pytest
from pathlib import Path

# Add project root to sys.path for test discovery
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Setup temporary test database
test_db_dir = tempfile.mkdtemp()
test_db_path = os.path.join(test_db_dir, "test_mandates.db")
os.environ["DATABASE_PATH"] = test_db_path

import src.config
src.config.DATABASE_PATH = test_db_path

import src.database
src.database.DATABASE_PATH = test_db_path
src.database.init_database()


@pytest.fixture(autouse=True)
def clean_test_database():
    """Reset database tables before each test to guarantee test isolation."""
    conn = src.database.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM mandate_failures")
    cursor.execute("DELETE FROM audit_log")
    cursor.execute("DELETE FROM agent_decisions")
    cursor.execute("DELETE FROM notification_log")
    conn.commit()
    conn.close()
    yield
