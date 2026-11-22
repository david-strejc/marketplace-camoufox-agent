from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marketplace_agent.models import Lead

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    listing_id TEXT NOT NULL UNIQUE,
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    attempts INTEGER NOT NULL DEFAULT 0,
    listing_url TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(profile_id, listing_id)
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, scheduled_for);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_key TEXT NOT NULL,
    phone TEXT NOT NULL,
    sender TEXT NOT NULL,
    message TEXT,
    observed_at TEXT NOT NULL,
    delivered_at TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE(profile_id, source, source_key, phone)
);

CREATE TABLE IF NOT EXISTS selector_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    target TEXT NOT NULL,
    selector TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
);
"""


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    profile_id: str
    listing_id: str
    scheduled_for: datetime
    attempts: int


@dataclass(frozen=True, slots=True)
class StoredLead:
    id: int
    lead: Lead


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def scheduled_listing_ids(self) -> set[str]:
        rows = self.db.execute("SELECT listing_id FROM jobs").fetchall()
        return {str(row["listing_id"]) for row in rows}

    def add_job(self, profile_id: str, listing_id: str, scheduled_for: datetime) -> int:
        cursor = self.db.execute(
            "INSERT INTO jobs(profile_id, listing_id, scheduled_for, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (profile_id, listing_id, scheduled_for.isoformat(), self._now()),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def jobs_for_day(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE scheduled_for >= ? AND scheduled_for < ? "
            "ORDER BY scheduled_for",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [dict(row) for row in rows]

    def recover_interrupted(self) -> int:
        cursor = self.db.execute(
            "UPDATE jobs SET status='failed', last_error=?, updated_at=? WHERE status='running'",
            ("process stopped while the job was running; inspect Facebook before retrying", self._now()),
        )
        self.db.commit()
        return cursor.rowcount

    def preview_due(self, now: datetime, limit: int = 100) -> list[Job]:
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE status='scheduled' AND scheduled_for <= ? "
            "ORDER BY scheduled_for LIMIT ?",
            (now.isoformat(), limit),
        ).fetchall()
        return [
            Job(
                id=int(row["id"]),
                profile_id=str(row["profile_id"]),
                listing_id=str(row["listing_id"]),
                scheduled_for=datetime.fromisoformat(str(row["scheduled_for"])),
                attempts=int(row["attempts"]),
            )
            for row in rows
        ]

    def claim_due(self, now: datetime, limit: int = 100) -> list[Job]:
        self.db.execute("BEGIN IMMEDIATE")
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE status='scheduled' AND scheduled_for <= ? "
            "ORDER BY scheduled_for LIMIT ?",
            (now.isoformat(), limit),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            self.db.execute(
                f"UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? "
                f"WHERE id IN ({placeholders})",
                (self._now(), *ids),
            )
        self.db.commit()
        return [
            Job(
                id=int(row["id"]),
                profile_id=str(row["profile_id"]),
                listing_id=str(row["listing_id"]),
                scheduled_for=datetime.fromisoformat(str(row["scheduled_for"])),
                attempts=int(row["attempts"]) + 1,
            )
            for row in rows
        ]

    def finish_job(
        self,
        job_id: int,
        status: str,
        listing_url: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in {"dry_run", "published", "failed", "unknown"}:
            raise ValueError(f"invalid job status: {status}")
        self.db.execute(
            "UPDATE jobs SET status=?, listing_url=?, last_error=?, updated_at=? WHERE id=?",
            (status, listing_url, error, self._now(), job_id),
        )
        self.db.commit()

    def retry_job(self, job_id: int) -> None:
        cursor = self.db.execute(
            "UPDATE jobs SET status='scheduled', last_error=NULL, updated_at=? "
            "WHERE id=? AND status='failed'",
            (self._now(), job_id),
        )
        self.db.commit()
        if cursor.rowcount != 1:
            raise ValueError("only a failed job can be retried; unknown jobs need manual reconciliation")

    def add_leads(self, leads: list[Lead]) -> int:
        inserted = 0
        for lead in leads:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO leads(profile_id, source, source_url, source_key, phone, "
                "sender, message, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lead.profile_id,
                    lead.source,
                    lead.source_url,
                    lead.source_key,
                    lead.phone,
                    lead.sender,
                    lead.message,
                    lead.observed_at.isoformat(),
                ),
            )
            inserted += cursor.rowcount
        self.db.commit()
        return inserted

    def undelivered_leads(self, limit: int = 500) -> list[StoredLead]:
        rows = self.db.execute(
            "SELECT * FROM leads WHERE delivered_at IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [self._stored_lead(row) for row in rows]

    @staticmethod
    def _stored_lead(row: sqlite3.Row) -> StoredLead:
        return StoredLead(
            id=int(row["id"]),
            lead=Lead(
                profile_id=str(row["profile_id"]),
                source=str(row["source"]),
                source_url=str(row["source_url"]),
                source_key=str(row["source_key"]),
                phone=str(row["phone"]),
                sender=str(row["sender"]),
                message=row["message"],
                observed_at=datetime.fromisoformat(str(row["observed_at"])),
            ),
        )

    def mark_leads_delivered(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self.db.execute(
            f"UPDATE leads SET delivered_at=?, last_error=NULL WHERE id IN ({placeholders})",
            (self._now(), *ids),
        )
        self.db.commit()

    def mark_lead_delivery_failed(self, ids: list[int], error: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self.db.execute(
            f"UPDATE leads SET delivery_attempts=delivery_attempts+1, last_error=? "
            f"WHERE id IN ({placeholders})",
            (error[:1000], *ids),
        )
        self.db.commit()

    def export_leads(self, output: Path) -> int:
        rows = self.db.execute("SELECT * FROM leads ORDER BY observed_at, id").fetchall()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "id",
                    "observed_at",
                    "profile_id",
                    "source",
                    "phone",
                    "sender",
                    "source_url",
                    "message",
                    "delivered_at",
                ]
            )
            for row in rows:
                writer.writerow([row[name] for name in ("id", "observed_at", "profile_id", "source", "phone", "sender", "source_url", "message", "delivered_at")])
        return len(rows)

    def add_selector_proposal(
        self, profile_id: str, target: str, selector: str, rationale: str
    ) -> None:
        self.db.execute(
            "INSERT INTO selector_proposals(created_at, profile_id, target, selector, rationale) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._now(), profile_id, target, selector, rationale),
        )
        self.db.commit()

    def metrics(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table, key in (("jobs", "jobs"), ("leads", "leads")):
            rows = self.db.execute(
                f"SELECT status AS state, COUNT(*) AS count FROM {table} GROUP BY status"
                if table == "jobs"
                else "SELECT CASE WHEN delivered_at IS NULL THEN 'pending' ELSE 'delivered' END "
                "AS state, COUNT(*) AS count FROM leads GROUP BY delivered_at IS NULL"
            ).fetchall()
            result[key] = {str(row["state"]): int(row["count"]) for row in rows}
        errors = self.db.execute(
            "SELECT last_error, COUNT(*) AS count FROM jobs WHERE last_error IS NOT NULL "
            "GROUP BY last_error ORDER BY count DESC LIMIT 10"
        ).fetchall()
        result["top_errors"] = [
            {"error": str(row["last_error"]), "count": int(row["count"])} for row in errors
        ]
        result["selector_proposals"] = [
            dict(row)
            for row in self.db.execute(
                "SELECT target, selector, rationale, status FROM selector_proposals "
                "WHERE status='proposed' ORDER BY id DESC LIMIT 50"
            ).fetchall()
        ]
        return result

    def export_metrics_json(self) -> str:
        return json.dumps(self.metrics(), ensure_ascii=False, indent=2)
