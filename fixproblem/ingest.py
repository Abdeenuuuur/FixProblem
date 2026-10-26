"""Import tickets from a helpdesk CSV export, with validation.

Rows are rejected (not silently dropped) when a required field is missing or a
timestamp cannot be parsed. Ticket IDs already in the database are counted as
duplicates and skipped, so re-importing the same export is safe.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

REQUIRED = ("ticket_id", "created_at", "body")
OPTIONAL = ("channel", "customer_id", "customer_email", "customer_tier", "subject", "agent_tag")
DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M")
VALID_TIERS = {"standard", "premium", "business"}
MAX_BODY = 10_000


class ImportFormatError(ValueError):
    """The file cannot be read as a ticket export at all."""


@dataclass
class ImportResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: list[tuple[int, str]] = field(default_factory=list)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def parse_timestamp(value: str) -> str:
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp '{value}'")


def validate_row(row: dict) -> dict:
    for col in REQUIRED:
        if not (row.get(col) or "").strip():
            raise ValueError(f"missing {col}")
    clean = {c: (row.get(c) or "").strip() for c in REQUIRED + OPTIONAL}
    clean["created_at"] = parse_timestamp(clean["created_at"])
    clean["body"] = clean["body"][:MAX_BODY]
    tier = clean["customer_tier"].lower()
    clean["customer_tier"] = tier if tier in VALID_TIERS else "standard"
    return clean


def import_csv(conn: sqlite3.Connection, data: str | bytes, filename: str = "upload.csv",
               dry_run: bool = False) -> ImportResult:
    """Import tickets. With dry_run=True nothing is written: the result shows what *would* happen."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportFormatError("file is not UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(data))
    if not reader.fieldnames:
        raise ImportFormatError("file is empty")
    missing = [c for c in REQUIRED if c not in reader.fieldnames]
    if missing:
        raise ImportFormatError(f"missing required columns: {', '.join(missing)}")

    result = ImportResult()
    existing = {r[0] for r in conn.execute("SELECT ticket_id FROM tickets")}
    seen_in_file: set[str] = set()
    for line_no, row in enumerate(reader, start=2):
        try:
            clean = validate_row(row)
        except ValueError as exc:
            result.rejected.append((line_no, str(exc)))
            continue
        tid = clean["ticket_id"]
        if tid in existing or tid in seen_in_file:
            result.duplicates += 1
            continue
        seen_in_file.add(tid)
        if dry_run:                      # validation only: count, never write
            result.accepted += 1
            continue
        conn.execute(
            "INSERT INTO tickets (ticket_id, created_at, channel, customer_id, customer_email, customer_tier,"
            " subject, body, agent_tag) VALUES (?,?,?,?,?,?,?,?,?)",
            tuple(clean[c] for c in ("ticket_id", "created_at", "channel", "customer_id", "customer_email",
                                     "customer_tier", "subject", "body", "agent_tag")),
        )
        conn.execute("INSERT INTO tickets_fts (ticket_id, subject, body) VALUES (?,?,?)",
                     (tid, clean["subject"], clean["body"]))
        result.accepted += 1
    if dry_run:
        return result
    conn.execute(
        "INSERT INTO import_log (filename, accepted, rejected, duplicates, errors) VALUES (?,?,?,?,?)",
        (filename, result.accepted, result.rejected_count, result.duplicates, json.dumps(result.rejected[:50])),
    )
    conn.commit()
    return result
