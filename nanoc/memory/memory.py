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
                    project_id TEXT,
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
            # Event Bus (Immutable, Ordered)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT,
                    payload TEXT,
                    schema_version TEXT,
                    timestamp TIMESTAMP
                )
            ''')
            # Metrics (Telemetry)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT,
                    value REAL,
                    unit TEXT,
                    tags TEXT,
                    timestamp TIMESTAMP
                )
            ''')
            conn.commit()

    def publish_event(self, topic: str, payload: Dict[str, Any], schema_version: str = "1.0"):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO events (topic, payload, schema_version, timestamp) VALUES (?, ?, ?, ?)',
                           (topic, json.dumps(payload), schema_version, datetime.now()))
            conn.commit()
            return cursor.lastrowid

    def get_metrics(self, name: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if name:
                cursor.execute('SELECT * FROM metrics WHERE metric_name = ? ORDER BY timestamp DESC LIMIT ?', (name, limit))
            else:
                cursor.execute('SELECT * FROM metrics ORDER BY timestamp DESC LIMIT ?', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_events(self, topic: Optional[str] = None, since_id: int = 0) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if topic:
                cursor.execute('SELECT * FROM events WHERE topic = ? AND id > ? ORDER BY id ASC', (topic, since_id))
            else:
                cursor.execute('SELECT * FROM events WHERE id > ? ORDER BY id ASC', (since_id,))
            return [dict(row) for row in cursor.fetchall()]

    def record_metric(self, name: str, value: float, unit: str = "", tags: Dict[str, str] = None):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO metrics (metric_name, value, unit, tags, timestamp) VALUES (?, ?, ?, ?, ?)',
                           (name, value, unit, json.dumps(tags or {}), datetime.now()))
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

    def get_knowledge(self, key: str) -> Optional[Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM knowledge WHERE key = ?', (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    def create_task(self, description: str, assigned_to: Optional[str] = None, parent_id: Optional[int] = None, project_id: Optional[str] = None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tasks (description, assigned_to, status, parent_id, project_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (description, assigned_to, 'pending', parent_id, project_id, datetime.now(), datetime.now()))
            conn.commit()
            return cursor.lastrowid
