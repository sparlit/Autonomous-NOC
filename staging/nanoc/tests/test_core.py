import pytest
import os
from nanoc.memory.memory import Memory
from nanoc.core.config import settings

def test_memory_init():
    db_path = "nanoc/memory/test.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    mem = Memory(db_path)
    mem.add_log("test_agent", "hello world")
    # Verify log was added
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM logs")
        row = cursor.fetchone()
        assert row[0] == "hello world"
    os.remove(db_path)

def test_config_defaults():
    assert settings.PROJECT_NAME == "NANOC"
