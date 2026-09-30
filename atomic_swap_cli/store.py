"""SQLite-backed persistence for swaps so the CLI is resumable across
process restarts. Each swap is stored as a JSON blob keyed by swap_id;
this keeps the schema stable while `Swap` gains fields over time.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .models import Swap

_SCHEMA = """
CREATE TABLE IF NOT EXISTS swaps (
    swap_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    payment_hash TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_swaps_payment_hash ON swaps(payment_hash);
"""


class SwapStore:
    """Thin repository wrapping a sqlite3 connection.

    Safe to instantiate against `:memory:` for tests.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SwapStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def save(self, swap: Swap) -> None:
        data = json.dumps(swap.to_dict())
        self._conn.execute(
            """
            INSERT INTO swaps (swap_id, state, payment_hash, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(swap_id) DO UPDATE SET
                state=excluded.state,
                payment_hash=excluded.payment_hash,
                data=excluded.data,
                updated_at=excluded.updated_at
            """,
            (swap.swap_id, swap.state.value, swap.payment_hash, data, swap.created_at, swap.updated_at),
        )
        self._conn.commit()

    def get(self, swap_id: str) -> Optional[Swap]:
        row = self._conn.execute("SELECT data FROM swaps WHERE swap_id = ?", (swap_id,)).fetchone()
        if row is None:
            return None
        return Swap.from_dict(json.loads(row[0]))

    def get_by_payment_hash(self, payment_hash: str) -> Optional[Swap]:
        row = self._conn.execute(
            "SELECT data FROM swaps WHERE payment_hash = ?", (payment_hash,)
        ).fetchone()
        if row is None:
            return None
        return Swap.from_dict(json.loads(row[0]))

    def list_all(self) -> list[Swap]:
        rows = self._conn.execute("SELECT data FROM swaps ORDER BY created_at DESC").fetchall()
        return [Swap.from_dict(json.loads(r[0])) for r in rows]

    def list_non_terminal(self) -> list[Swap]:
        terminal = {"settled", "refunded", "expired", "failed"}
        return [s for s in self.list_all() if s.state.value not in terminal]

    def delete(self, swap_id: str) -> None:
        self._conn.execute("DELETE FROM swaps WHERE swap_id = ?", (swap_id,))
        self._conn.commit()
