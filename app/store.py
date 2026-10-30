"""Stockage SQLite + fichiers images.

Les images sont écrites sur disque dans DATA_DIR/images/<session>/ ; la base ne
contient que des métadonnées et les résultats. Aucune image ni aucun texte de
document n'est écrit dans les journaux.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid

from .models import COLLECTING, Page, Question, Session

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  psid TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  results TEXT
);
CREATE TABLE IF NOT EXISTS pages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  path TEXT NOT NULL,
  width INTEGER, height INTEGER, blur_score REAL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_mids(mid TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage(
  ts REAL NOT NULL, provider TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, cost REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pages_session ON pages(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_psid ON sessions(psid);
"""


class Store:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.images_dir = os.path.join(data_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)
        os.chmod(data_dir, 0o700)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(os.path.join(data_dir, "studylens.db"), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ---------------------------------------------------------- sessions
    def _session(self, row: sqlite3.Row | None) -> Session | None:
        if row is None:
            return None
        return Session(row["id"], row["psid"], row["state"], row["created_at"], row["updated_at"])

    def create_session(self, psid: str) -> Session:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO sessions(psid,state,created_at,updated_at) VALUES(?,?,?,?)",
                (psid, COLLECTING, now, now),
            )
            self._db.commit()
            return Session(cur.lastrowid, psid, COLLECTING, now, now)

    def get_session(self, sid: int) -> Session | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return self._session(row)

    def latest_session(self, psid: str) -> Session | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM sessions WHERE psid=? ORDER BY id DESC LIMIT 1", (psid,)
            ).fetchone()
        return self._session(row)

    def set_state(self, sid: int, state: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sessions SET state=?, updated_at=? WHERE id=?", (state, time.time(), sid)
            )
            self._db.commit()

    def count_sessions_since(self, psid: str, since: float) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE psid=? AND created_at>=?", (psid, since)
            ).fetchone()
        return int(row["n"])

    # ---------------------------------------------------------- pages
    def add_page(self, sid: int, data: bytes, width: int, height: int, blur: float) -> Page:
        folder = os.path.join(self.images_dir, str(sid))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{uuid.uuid4().hex}.jpg")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with self._lock:
            n = self._db.execute(
                "SELECT COUNT(*) AS n FROM pages WHERE session_id=?", (sid,)
            ).fetchone()["n"]
            idx = int(n) + 1
            cur = self._db.execute(
                "INSERT INTO pages(session_id,idx,path,width,height,blur_score,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (sid, idx, path, width, height, blur, time.time()),
            )
            self._db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), sid))
            self._db.commit()
            return Page(cur.lastrowid, sid, idx, path, width, height, blur)

    def pages(self, sid: int) -> list[Page]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM pages WHERE session_id=? ORDER BY idx", (sid,)
            ).fetchall()
        return [
            Page(r["id"], r["session_id"], r["idx"], r["path"], r["width"], r["height"], r["blur_score"])
            for r in rows
        ]

    def page_count(self, sid: int) -> int:
        with self._lock:
            return int(
                self._db.execute(
                    "SELECT COUNT(*) AS n FROM pages WHERE session_id=?", (sid,)
                ).fetchone()["n"]
            )

    def delete_last_page(self, sid: int) -> bool:
        pages = self.pages(sid)
        if not pages:
            return False
        last = pages[-1]
        self._remove_file(last.path)
        with self._lock:
            self._db.execute("DELETE FROM pages WHERE id=?", (last.id,))
            self._db.commit()
        return True

    def read_page(self, page: Page) -> bytes:
        with open(page.path, "rb") as fh:
            return fh.read()

    def delete_session_images(self, sid: int) -> None:
        for p in self.pages(sid):
            self._remove_file(p.path)
        with self._lock:
            self._db.execute("DELETE FROM pages WHERE session_id=?", (sid,))
            self._db.commit()
        shutil.rmtree(os.path.join(self.images_dir, str(sid)), ignore_errors=True)

    # ---------------------------------------------------------- résultats
    def save_results(self, sid: int, questions: list[Question]) -> None:
        payload = json.dumps([q.to_dict() for q in questions], ensure_ascii=False)
        with self._lock:
            self._db.execute("UPDATE sessions SET results=? WHERE id=?", (payload, sid))
            self._db.commit()

    def get_results(self, sid: int) -> list[Question]:
        with self._lock:
            row = self._db.execute("SELECT results FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row or not row["results"]:
            return []
        return [Question.from_dict(d) for d in json.loads(row["results"])]

    def delete_session(self, sid: int) -> None:
        self.delete_session_images(sid)
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            self._db.commit()

    # ---------------------------------------------------------- idempotence
    def first_time_mid(self, mid: str) -> bool:
        """True si ce message n'a jamais été traité (Meta peut renvoyer un webhook)."""
        if not mid:
            return True
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO processed_mids(mid,ts) VALUES(?,?)", (mid, time.time())
                )
                self._db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # ---------------------------------------------------------- usage / budget
    def record_usage(self, provider: str, in_tok: int, out_tok: int, cost: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO usage(ts,provider,input_tokens,output_tokens,cost) VALUES(?,?,?,?,?)",
                (time.time(), provider, in_tok, out_tok, cost),
            )
            self._db.commit()

    def spent_since(self, since: float) -> float:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(cost),0) AS s FROM usage WHERE ts>=?", (since,)
            ).fetchone()
        return float(row["s"])

    def calls_since(self, since: float) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM usage WHERE ts>=?", (since,)
            ).fetchone()
        return int(row["n"])

    # ---------------------------------------------------------- rétention
    def purge(self, *, image_ttl: float, session_ttl: float, keep_history: bool) -> dict[str, int]:
        """Supprime les images trop anciennes, puis les sessions échues."""
        now = time.time()
        removed_images = removed_sessions = 0
        with self._lock:
            old_img = self._db.execute(
                "SELECT DISTINCT session_id FROM pages WHERE created_at<?", (now - image_ttl,)
            ).fetchall()
        for r in old_img:
            self.delete_session_images(r["session_id"])
            removed_images += 1
        if not keep_history:
            with self._lock:
                old_sessions = self._db.execute(
                    "SELECT id FROM sessions WHERE updated_at<?", (now - session_ttl,)
                ).fetchall()
            for r in old_sessions:
                self.delete_session(r["id"])
                removed_sessions += 1
            with self._lock:
                self._db.execute("DELETE FROM processed_mids WHERE ts<?", (now - 7 * 86400,))
                self._db.commit()
        return {"images": removed_images, "sessions": removed_sessions}

    @staticmethod
    def _remove_file(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def close(self) -> None:
        with self._lock:
            self._db.close()
