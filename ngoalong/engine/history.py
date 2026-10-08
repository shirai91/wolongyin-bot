"""Run history (docs/06 P3 "Run history"): one record per run, kept in ``data/history.sqlite3``.

A ``RunRecord`` holds the start and end time, the task, the routine that started it (if any), the
result, the counters (clicks, progress and whatever the feature counted with ``ctx.count``), the
failure screenshots and the run's log folder.

* ``HistoryStore`` is the Qt-free SQLite store (thread-safe; ``None`` path = in memory).
* ``HistoryRecorder`` listens to the runners' ``finished`` signals and stores every run, so
  manual runs, routine steps and multi-window runs all end up in the history.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Signal, Slot

from ngoalong.engine.runner import RunResult, RunStatus

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_MAX_RECORDS = 5000

STATUS_TEXT: dict[str, str] = {
    RunStatus.DONE.value: "Hoàn thành",
    RunStatus.STOPPED.value: "Đã dừng",
    RunStatus.FAILED.value: "Lỗi",
}

COUNTER_TEXT: dict[str, str] = {
    "clicks": "lượt nhấp",
    "progress": "tiến độ",
    "progress_total": "tổng",
    "spins": "lượt quay",
    "floors": "tầng",
    "bosses": "boss",
    "battles": "trận",
    "rounds": "lượt",
    "x2": "quà x2",
    "chests": "lần mở rương",
    "maps": "phó bản",
}


def _ts(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _from_epoch(value: float) -> datetime:
    return datetime.fromtimestamp(value).replace(microsecond=0)


class RunRecord(BaseModel):
    """One run in the history (times are local, naive, second precision)."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    task_id: str
    task_title: str = ""
    started_at: datetime
    ended_at: datetime
    status: str = RunStatus.DONE.value
    message: str = ""
    counters: dict[str, int] = Field(default_factory=dict)
    screenshots: list[str] = Field(default_factory=list)
    run_dir: str = ""
    window: str = ""
    routine_id: str = ""
    routine_name: str = ""
    attempt: int = 1

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.ended_at - self.started_at).total_seconds())

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    def counters_text(self) -> str:
        c = dict(self.counters)
        parts: list[str] = []
        if "progress" in c:
            done, total = c.pop("progress"), c.pop("progress_total", None)
            parts.append(f"tiến độ {done}" + (f"/{total}" if total is not None else ""))
        c.pop("progress_total", None)
        parts += [f"{COUNTER_TEXT.get(k, k)} {v}" for k, v in sorted(c.items())]
        return ", ".join(parts)

    @classmethod
    def from_result(
        cls, result: RunResult, task_title: str = "", window: str = "",
        meta: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        """Build a record from a ``RunResult`` (its ``meta`` carries the routine tags)."""
        m = dict(result.meta or {})
        m.update(meta or {})
        ended = _from_epoch(result.ended_at) if result.ended_at else datetime.now().replace(microsecond=0)
        if result.started_at:
            started = _from_epoch(result.started_at)
        else:
            started = (ended - timedelta(seconds=max(0.0, result.duration_s))).replace(microsecond=0)
        shots = [str(result.screenshot)] if result.screenshot is not None else []
        return cls(
            task_id=result.task_id,
            task_title=result.task_title or task_title or result.task_id,
            started_at=started, ended_at=max(started, ended),
            status=RunStatus(result.status).value, message=result.message,
            counters=dict(result.counters or {}), screenshots=shots,
            run_dir=str(result.run_dir) if result.run_dir is not None else "",
            window=str(m.get("window") or window or ""),
            routine_id=str(m.get("routine_id") or ""), routine_name=str(m.get("routine_name") or ""),
            attempt=int(m.get("attempt") or 1),  # type: ignore[call-overload]
        )


@dataclass(frozen=True, slots=True)
class TaskSummary:
    task_id: str
    task_title: str
    runs: int
    done: int
    failed: int
    stopped: int
    counters: dict[str, int]


_COLUMNS = ("id", "task_id", "task_title", "started_at", "ended_at", "status", "message", "counters",
            "screenshots", "run_dir", "window", "routine_id", "routine_name", "attempt")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    task_title TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    counters TEXT NOT NULL DEFAULT '{}',
    screenshots TEXT NOT NULL DEFAULT '[]',
    run_dir TEXT NOT NULL DEFAULT '',
    window TEXT NOT NULL DEFAULT '',
    routine_id TEXT NOT NULL DEFAULT '',
    routine_name TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at);
CREATE INDEX IF NOT EXISTS runs_task ON runs(task_id);
"""


class HistoryStore:
    """SQLite run history. Opened lazily (no file until the first use); every call is locked."""

    def __init__(self, path: str | Path | None, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self.path = Path(path) if path is not None else None
        self.max_records = max(1, int(max_records))
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # --- connection -----------------------------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        if self.path is None:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        try:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def _connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        try:
            self._conn = self._open()
        except sqlite3.DatabaseError as exc:
            if self.path is None or not self.path.exists():
                raise
            backup = self.path.with_name(self.path.name + ".bak")
            log.error("history database %s is broken (%s); kept as %s", self.path, exc, backup)
            os.replace(self.path, backup)
            self._conn = self._open()
        return self._conn

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connection()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # --- rows -----------------------------------------------------------------------------------
    @staticmethod
    def _row(row: tuple[Any, ...]) -> RunRecord:
        d = dict(zip(_COLUMNS, row, strict=True))
        d["counters"] = json.loads(d["counters"] or "{}")
        d["screenshots"] = json.loads(d["screenshots"] or "[]")
        return RunRecord.model_validate(d)

    # --- writing --------------------------------------------------------------------------------
    def add(self, record: RunRecord) -> RunRecord:
        """Store ``record``; returns it with its id. Old records beyond ``max_records`` are pruned."""
        values = (
            record.task_id, record.task_title, _ts(record.started_at), _ts(record.ended_at),
            record.status, record.message, json.dumps(record.counters, ensure_ascii=False),
            json.dumps(record.screenshots, ensure_ascii=False), record.run_dir, record.window,
            record.routine_id, record.routine_name, int(record.attempt),
        )
        with self._db() as db:
            cur = db.execute(
                f"INSERT INTO runs ({', '.join(_COLUMNS[1:])}) VALUES ({', '.join('?' * (len(_COLUMNS) - 1))})",
                values,
            )
            rid = int(cur.lastrowid or 0)
            db.execute(
                "DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)",
                (self.max_records,),
            )
        return record.model_copy(update={"id": rid})

    def delete(self, ids: Iterable[int]) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        with self._db() as db:
            cur = db.execute(f"DELETE FROM runs WHERE id IN ({', '.join('?' * len(ids))})", ids)
            return cur.rowcount

    def clear(self) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM runs").rowcount

    def prune(self, older_than_days: float) -> int:
        """Remove the records that started more than ``older_than_days`` ago."""
        cutoff = datetime.now() - timedelta(days=max(0.0, older_than_days))
        with self._db() as db:
            return db.execute("DELETE FROM runs WHERE started_at < ?", (_ts(cutoff),)).rowcount

    # --- reading --------------------------------------------------------------------------------
    @staticmethod
    def _where(task_id: str | None, status: str | None, routine_id: str | None,
               since: datetime | None, until: datetime | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        for col, val in (("task_id", task_id), ("status", status), ("routine_id", routine_id)):
            if val:
                clauses.append(f"{col} = ?")
                args.append(val)
        if since is not None:
            clauses.append("started_at >= ?")
            args.append(_ts(since))
        if until is not None:
            clauses.append("started_at < ?")
            args.append(_ts(until))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def get(self, record_id: int) -> RunRecord | None:
        with self._db() as db:
            row = db.execute(f"SELECT {', '.join(_COLUMNS)} FROM runs WHERE id = ?", (int(record_id),)).fetchone()
        return self._row(row) if row else None

    def query(
        self, limit: int = 200, offset: int = 0, task_id: str | None = None, status: str | None = None,
        routine_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
    ) -> list[RunRecord]:
        """Records, newest first, filtered by task, status, routine and start time."""
        where, args = self._where(task_id, status, routine_id, since, until)
        sql = (f"SELECT {', '.join(_COLUMNS)} FROM runs{where} "
               "ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?")
        with self._db() as db:
            rows = db.execute(sql, [*args, max(0, int(limit)), max(0, int(offset))]).fetchall()
        return [self._row(r) for r in rows]

    def count(self, task_id: str | None = None, status: str | None = None, routine_id: str | None = None,
              since: datetime | None = None, until: datetime | None = None) -> int:
        where, args = self._where(task_id, status, routine_id, since, until)
        with self._db() as db:
            return int(db.execute(f"SELECT COUNT(*) FROM runs{where}", args).fetchone()[0])

    def summary(self, since: datetime | None = None, until: datetime | None = None) -> list[TaskSummary]:
        """Per task: run counts by result and the summed counters (``progress_total`` excluded)."""
        acc: dict[str, dict[str, Any]] = {}
        for r in self.query(limit=self.max_records, since=since, until=until):
            a = acc.setdefault(r.task_id, {"title": r.task_title, "runs": 0, "counters": {},
                                           RunStatus.DONE.value: 0, RunStatus.FAILED.value: 0,
                                           RunStatus.STOPPED.value: 0})
            a["runs"] += 1
            if r.status in a:
                a[r.status] += 1
            for k, v in r.counters.items():
                if k != "progress_total":
                    a["counters"][k] = a["counters"].get(k, 0) + int(v)
        return [
            TaskSummary(tid, a["title"], a["runs"], a[RunStatus.DONE.value], a[RunStatus.FAILED.value],
                        a[RunStatus.STOPPED.value], a["counters"])
            for tid, a in sorted(acc.items(), key=lambda kv: kv[1]["title"])
        ]


class HistoryRecorder(QObject):
    """Stores every finished run. Lives in the UI thread (results are queued from the workers)."""

    recorded = Signal(object)  # RunRecord (with id)

    def __init__(self, store: HistoryStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store

    def watch(self, runner: QObject, window: Any = None) -> None:
        """Record the runs of a ``TaskRunner``; ``window`` is a label or a callable giving one."""
        def on_finished(result: RunResult) -> None:
            self.record(result, _label(window))

        runner.finished.connect(on_finished)  # type: ignore[attr-defined]

    def watch_manager(self, manager: QObject) -> None:
        """Record the runs of every ``RunnerManager`` slot (multi-window)."""
        manager.slot_finished.connect(self._on_slot_finished)  # type: ignore[attr-defined]

    @Slot(str, object)
    def _on_slot_finished(self, key: str, result: RunResult) -> None:
        title = key
        slots = getattr(self.sender(), "slots", None)
        if callable(slots):
            try:
                title = next((s.title for s in slots() if s.key == key), key)
            except Exception:
                title = key
        self.record(result, title)

    def record(self, result: RunResult, window: str = "") -> RunRecord | None:
        try:
            rec = self.store.add(RunRecord.from_result(result, window=window))
        except (sqlite3.Error, OSError, ValueError):
            log.exception("could not store the run of %s in the history", result.task_id)
            return None
        self.recorded.emit(rec)
        return rec

    def add(self, record: RunRecord) -> RunRecord | None:
        """Store a record built by the caller (e.g. a routine step that could not start)."""
        try:
            rec = self.store.add(record)
        except (sqlite3.Error, OSError, ValueError):
            log.exception("could not store a history record")
            return None
        self.recorded.emit(rec)
        return rec


def _label(window: Any) -> str:
    if callable(window):
        try:
            window = window()
        except Exception:
            return ""
    if window is None:
        return ""
    if isinstance(window, str):
        return window
    title = getattr(window, "title", None)
    return title if isinstance(title, str) else str(window)
