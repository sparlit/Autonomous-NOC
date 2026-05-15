import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

class Memory:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Agent registry
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    role TEXT,
                    status TEXT,
                    created_at TIMESTAMP
                )
            ''')
            # Task list
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER,
                    description TEXT,
                    assigned_to TEXT,
                    status TEXT,
                    result TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            ''')
            # Agent Logs (Thoughts/Actions)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT,
                    content TEXT,
                    timestamp TIMESTAMP
                )
            ''')
            # Knowledge Base / State
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS knowledge (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP
                )
            ''')
            conn.commit()

    def add_log(self, agent_id: str, content: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO logs (agent_id, content, timestamp) VALUES (?, ?, ?)',
                           (agent_id, content, datetime.now()))
            conn.commit()

    def upsert_knowledge(self, key: str, value: Any):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO knowledge (key, value, updated_at) VALUES (?, ?, ?)',
                           (key, json.dumps(value), datetime.now()))
            conn.commit()

    def create_task(self, description: str, assigned_to: Optional[str] = None, parent_id: Optional[int] = None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO tasks (description, assigned_to, status, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
                           (description, assigned_to, 'pending', parent_id, datetime.now(), datetime.now()))
            conn.commit()
            return cursor.lastrowid
