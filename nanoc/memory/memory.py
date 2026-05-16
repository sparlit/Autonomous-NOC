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
        """
        Publish an immutable event record to the events table.
        """
        # Ensure payload is JSON serializable, handle potential binary data
        def sanitize(obj):
            if isinstance(obj, bytes):
                return obj.decode('utf-8', errors='replace')
            if isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [sanitize(i) for i in obj]
            return obj

        sanitized_payload = sanitize(payload)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO events (topic, payload, schema_version, timestamp) VALUES (?, ?, ?, ?)',
                           (topic, json.dumps(sanitized_payload), schema_version, datetime.now()))
            conn.commit()
            return cursor.lastrowid

    def get_metrics(self, name: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve recent metric records from the metrics table, optionally filtered by metric name.
        
        Parameters:
        	name (Optional[str]): If provided, only metrics with this metric_name are returned.
        	limit (int): Maximum number of rows to return, ordered by timestamp descending. Defaults to 100.
        
        Returns:
        	List[Dict[str, Any]]: A list of dictionaries representing metric rows; each dictionary contains the columns from the metrics table (for example: id, metric_name, value, unit, tags, timestamp).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if name:
                cursor.execute('SELECT * FROM metrics WHERE metric_name = ? ORDER BY timestamp DESC LIMIT ?', (name, limit))
            else:
                cursor.execute('SELECT * FROM metrics ORDER BY timestamp DESC LIMIT ?', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_events(self, topic: Optional[str] = None, since_id: int = 0) -> List[Dict[str, Any]]:
        """
        Retrieve events published after a given event ID, optionally filtered by topic.
        
        Parameters:
        	topic (Optional[str]): If provided, only events with this topic are returned.
        	since_id (int): Only events with an `id` greater than this value are returned.
        
        Returns:
        	events (List[Dict[str, Any]]): List of event records as dictionaries, ordered by `id` ascending.
        """
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
        # Ensure content is string
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        elif not isinstance(content, str):
            content = str(content)

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

    def search_knowledge(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search for knowledge entries containing the query string in their key or value.
        (Primitive RAG capability)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Use LIKE for simple keyword matching
            cursor.execute('''
                SELECT key, value FROM knowledge
                WHERE key LIKE ? OR value LIKE ?
                ORDER BY updated_at DESC LIMIT ?
            ''', (f"%{query}%", f"%{query}%", limit))
            return [dict(row) for row in cursor.fetchall()]

    def create_task(self, description: str, assigned_to: Optional[str] = None, parent_id: Optional[int] = None, project_id: Optional[str] = None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tasks (description, assigned_to, status, parent_id, project_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (description, assigned_to, 'pending', parent_id, project_id, datetime.now(), datetime.now()))
            conn.commit()
            return cursor.lastrowid
