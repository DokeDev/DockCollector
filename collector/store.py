import json
import sqlite3
import threading
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS targets (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          rule_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS results (
          id INTEGER PRIMARY KEY AUTOINCREMENT, target_id TEXT NOT NULL,
          board_name TEXT, url TEXT NOT NULL, title TEXT, list_time TEXT,
          data_json TEXT NOT NULL, collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(target_id, url)
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, target_id TEXT NOT NULL,
          level TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS deleted_targets (
          id TEXT PRIMARY KEY, deleted_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS checkpoints (
          target_id TEXT PRIMARY KEY, data_json TEXT NOT NULL,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        self.db.commit()

    def seed(self, target_id, name, rule):
        with self.lock:
            if self.db.execute("SELECT 1 FROM deleted_targets WHERE id=?", (target_id,)).fetchone():
                return
            self.db.execute("INSERT OR IGNORE INTO targets(id,name,rule_json) VALUES(?,?,?)",
                            (target_id, name, json.dumps(rule, ensure_ascii=False)))
            self.db.commit()

    def delete_target(self, target_id):
        with self.lock:
            exists = self.db.execute("SELECT 1 FROM targets WHERE id=?", (target_id,)).fetchone()
            if not exists:
                return False
            self.db.execute("DELETE FROM results WHERE target_id=?", (target_id,))
            self.db.execute("DELETE FROM events WHERE target_id=?", (target_id,))
            self.db.execute("DELETE FROM checkpoints WHERE target_id=?", (target_id,))
            self.db.execute("DELETE FROM targets WHERE id=?", (target_id,))
            self.db.execute("INSERT OR REPLACE INTO deleted_targets(id) VALUES(?)", (target_id,))
            self.db.commit()
            return True

    def targets(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM targets ORDER BY id").fetchall()
            return [dict(r) | {"rule": json.loads(r["rule_json"])} for r in rows]

    def target(self, target_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
            return None if not r else dict(r) | {"rule": json.loads(r["rule_json"])}

    def save_target(self, target_id, payload):
        rule = payload["rule"]
        with self.lock:
            self.db.execute("""INSERT INTO targets(id,name,enabled,rule_json) VALUES(?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,
              rule_json=excluded.rule_json,updated_at=CURRENT_TIMESTAMP""",
              (target_id, payload.get("name", target_id), int(payload.get("enabled", True)),
               json.dumps(rule, ensure_ascii=False)))
            self.db.commit()

    def add_result(self, target_id, board, url, title, list_time, data):
        with self.lock:
            cur = self.db.execute("""INSERT OR IGNORE INTO results
              (target_id,board_name,url,title,list_time,data_json) VALUES(?,?,?,?,?,?)""",
              (target_id, board, url, title, list_time, json.dumps(data, ensure_ascii=False)))
            self.db.commit()
            return cur.rowcount > 0

    def seen(self, target_id, url):
        with self.lock:
            return self.db.execute("SELECT 1 FROM results WHERE target_id=? AND url=?",
                                   (target_id, url)).fetchone() is not None

    def results(self, target_id, limit=1000, offset=0):
        with self.lock:
            rows = self.db.execute("SELECT * FROM results WHERE target_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                                   (target_id, limit, offset)).fetchall()
            return [dict(r) | {"data": json.loads(r["data_json"])} for r in rows]

    def result_count(self, target_id):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM results WHERE target_id=?", (target_id,)).fetchone()[0]

    def clear_results(self, target_id):
        with self.lock:
            count = self.db.execute("SELECT COUNT(*) FROM results WHERE target_id=?", (target_id,)).fetchone()[0]
            self.db.execute("DELETE FROM results WHERE target_id=?", (target_id,))
            self.db.commit()
            return count

    def save_checkpoint(self, target_id, data):
        with self.lock:
            self.db.execute("""INSERT INTO checkpoints(target_id,data_json) VALUES(?,?)
              ON CONFLICT(target_id) DO UPDATE SET data_json=excluded.data_json,
              updated_at=CURRENT_TIMESTAMP""",
              (target_id, json.dumps(data, ensure_ascii=False)))
            self.db.commit()

    def checkpoint(self, target_id):
        with self.lock:
            row = self.db.execute(
                "SELECT data_json,updated_at FROM checkpoints WHERE target_id=?", (target_id,)).fetchone()
            if not row: return None
            return json.loads(row["data_json"]) | {"updated_at": row["updated_at"]}

    def clear_checkpoint(self, target_id):
        with self.lock:
            self.db.execute("DELETE FROM checkpoints WHERE target_id=?", (target_id,))
            self.db.commit()

    def event(self, target_id, level, message):
        with self.lock:
            self.db.execute("INSERT INTO events(target_id,level,message) VALUES(?,?,?)",
                            (target_id, level, message))
            self.db.commit()

    def events(self, target_id, limit=100):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM events WHERE target_id=? ORDER BY id DESC LIMIT ?", (target_id, limit))]
