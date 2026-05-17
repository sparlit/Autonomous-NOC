import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

class Memory:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

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
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    priority INTEGER DEFAULT 0,
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
        
        The provided payload is serialized to JSON and stored together with the topic, schema version, and the current timestamp.
        
        Parameters:
            topic (str): Topic name categorizing the event.
            payload (Dict[str, Any]): JSON-serializable event payload to store.
            schema_version (str): Version identifier for the payload schema (defaults to "1.0").
        
        Returns:
            int: The newly inserted event row ID.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO events (topic, payload, schema_version, timestamp) VALUES (?, ?, ?, ?)',
                           (topic, json.dumps(payload), schema_version, datetime.now()))
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
        with self.get_connection() as conn:
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
        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if topic:
                cursor.execute('SELECT * FROM events WHERE topic = ? AND id > ? ORDER BY id ASC', (topic, since_id))
            else:
                cursor.execute('SELECT * FROM events WHERE id > ? ORDER BY id ASC', (since_id,))
            return [dict(row) for row in cursor.fetchall()]

    def record_metric(self, name: str, value: float, unit: str = "", tags: Dict[str, str] = None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO metrics (metric_name, value, unit, tags, timestamp) VALUES (?, ?, ?, ?, ?)',
                           (name, value, unit, json.dumps(tags or {}), datetime.now()))
            conn.commit()

    def add_log(self, agent_id: str, content: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO logs (agent_id, content, timestamp) VALUES (?, ?, ?)',
                           (agent_id, content, datetime.now()))
            conn.commit()

    def upsert_knowledge(self, key: str, value: Any):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO knowledge (key, value, updated_at) VALUES (?, ?, ?)',
                           (key, json.dumps(value), datetime.now()))
            conn.commit()

    def get_knowledge(self, key: str) -> Optional[Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM knowledge WHERE key = ?', (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    def create_task(self, description: str, assigned_to: Optional[str] = None, parent_id: Optional[int] = None, project_id: Optional[str] = None, max_retries: int = 3, priority: int = 0) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tasks (description, assigned_to, status, parent_id, project_id, max_retries, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (description, assigned_to, 'pending', parent_id, project_id, max_retries, priority, datetime.now(), datetime.now()))
            conn.commit()
            return cursor.lastrowid
