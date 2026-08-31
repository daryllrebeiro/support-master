"""Agent memory: SQLite FTS5 case similarity index for cross-run learning."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SimilarCase:
    case_id: str
    title: str
    root_cause: str
    resolution_summary: str
    similarity_rank: float
    resolved_repos: list[str] | None = None

    def to_context_block(self) -> str:
        block = (
            f"[Similar past case: {self.case_id}]\n"
            f"  Title: {self.title}\n"
            f"  Root cause: {self.root_cause}\n"
            f"  How it was resolved: {self.resolution_summary}\n"
        )
        if self.resolved_repos:
            block += f"  Repositories involved: {', '.join(self.resolved_repos)}\n"
        return block


class CaseMemoryStore:
    """
    SQLite-backed FTS5 similarity index that persists resolved case
    knowledge across runs for a given tenant.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        path = db_path or os.getenv("SUPPORTMASTER_MEMORY_DB", ".supportmaster/memory.db")
        self._db_path = str(path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS case_memory USING fts5(
                    case_id UNINDEXED,
                    tenant_id UNINDEXED,
                    title,
                    description,
                    root_cause,
                    resolution_summary,
                    tags
                );
                CREATE TABLE IF NOT EXISTS case_memory_repos (
                    case_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    resolved_repos TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (case_id, tenant_id)
                );
            """)
        finally:
            conn.close()

    def record(
        self,
        *,
        case_id: str,
        tenant_id: str,
        title: str,
        description: str,
        root_cause: str,
        resolution_summary: str,
        tags: list[str] | None = None,
        resolved_repos: list[str] | None = None,
    ) -> None:
        """Persist a resolved case into the memory index.

        ``resolved_repos`` records which repositories fixed the issue as
        ``provider:workspace/repo`` keys so future discovery runs can reuse
        the answer. Stored in a plain side table because FTS5 virtual tables
        cannot be altered to add columns.
        """
        tag_str = " ".join(tags or [])
        repos_json = json.dumps(list(resolved_repos or []))
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM case_memory WHERE case_id = ? AND tenant_id = ?",
                    (case_id, tenant_id),
                )
                conn.execute(
                    "INSERT INTO case_memory(case_id, tenant_id, title, description, root_cause, resolution_summary, tags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (case_id, tenant_id, title, description, root_cause, resolution_summary, tag_str),
                )
                conn.execute(
                    "INSERT INTO case_memory_repos(case_id, tenant_id, resolved_repos) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(case_id, tenant_id) DO UPDATE SET resolved_repos = excluded.resolved_repos",
                    (case_id, tenant_id, repos_json),
                )
        finally:
            conn.close()

    def retrieve_similar(
        self,
        query: str,
        tenant_id: str,
        top_k: int = 3,
    ) -> list[SimilarCase]:
        """Return the top-k most similar past cases for the given query text."""
        if not query.strip():
            return []
        words = [
            word for word in query.split() if word.isalnum() and len(word) > 2
        ]
        if not words:
            return []
        sanitized = " OR ".join(words)
        conn = self._connect()
        try:
            # FTS5 MATCH must reference the virtual table directly, so the
            # ranked match runs in a subquery before joining the repos
            # side table.
            rows = conn.execute(
                """
                SELECT f.case_id, f.title, f.root_cause, f.resolution_summary,
                       f.similarity_rank, r.resolved_repos
                FROM (
                    SELECT case_id, title, root_cause, resolution_summary,
                           rank AS similarity_rank
                    FROM case_memory
                    WHERE case_memory MATCH ? AND tenant_id = ?
                    ORDER BY rank
                    LIMIT ?
                ) f
                LEFT JOIN case_memory_repos r
                    ON r.case_id = f.case_id AND r.tenant_id = ?
                """,
                (sanitized, tenant_id, top_k, tenant_id),
            ).fetchall()

            results: list[SimilarCase] = []
            for row in rows:
                raw_repos = row["resolved_repos"] if "resolved_repos" in row.keys() else None
                try:
                    repos = json.loads(raw_repos) if raw_repos else []
                except (TypeError, ValueError):
                    repos = []
                results.append(
                    SimilarCase(
                        case_id=row["case_id"],
                        title=row["title"],
                        root_cause=row["root_cause"],
                        resolution_summary=row["resolution_summary"],
                        similarity_rank=float(row["similarity_rank"] or 0.0),
                        resolved_repos=[str(repo) for repo in repos],
                    )
                )
            return results
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
