"""Training datasets and jobs persistence."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def init_training_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS training_datasets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            dataset_type TEXT NOT NULL DEFAULT 'yolo',
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            local_path TEXT,
            remote_uri TEXT,
            stats_json TEXT NOT NULL DEFAULT '{}',
            manifest_json TEXT,
            error TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS training_jobs (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            job_type TEXT NOT NULL DEFAULT 'yolo_local',
            status TEXT NOT NULL DEFAULT 'pending',
            base_model TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            output_path TEXT,
            cloud_job_id TEXT,
            log_text TEXT,
            error TEXT,
            metrics_json TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_training_jobs_ds ON training_jobs(dataset_id, created_at DESC)"
    )
    conn.commit()


def _db():
    import basebuddy.modules.state as st
    return st.analytics_db


def save_dataset(rec: dict) -> dict:
    db = _db()
    now = time.time()
    rec = {**rec, "created_at": rec.get("created_at") or now}
    with db._connect() as conn:
        init_training_tables(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO training_datasets (
                id, name, dataset_type, created_at, status, local_path, remote_uri,
                stats_json, manifest_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec["id"],
                rec.get("name") or rec["id"],
                rec.get("dataset_type") or "yolo",
                rec["created_at"],
                rec.get("status") or "ready",
                rec.get("local_path"),
                rec.get("remote_uri"),
                json.dumps(rec.get("stats") or {}, default=str),
                json.dumps(rec.get("manifest") or {}, default=str) if rec.get("manifest") else None,
                rec.get("error"),
            ),
        )
        conn.commit()
    return rec


def update_dataset(dataset_id: str, **fields) -> Optional[dict]:
    ds = get_dataset(dataset_id)
    if not ds:
        return None
    merged = {**ds, **fields}
    if "stats" in fields:
        merged["stats"] = fields["stats"]
    save_dataset({
        "id": dataset_id,
        "name": merged.get("name"),
        "dataset_type": merged.get("dataset_type"),
        "created_at": merged.get("created_at"),
        "status": merged.get("status"),
        "local_path": merged.get("local_path"),
        "remote_uri": merged.get("remote_uri"),
        "stats": merged.get("stats"),
        "manifest": merged.get("manifest"),
        "error": merged.get("error"),
    })
    return get_dataset(dataset_id)


def get_dataset(dataset_id: str) -> Optional[dict]:
    db = _db()
    with db._connect() as conn:
        init_training_tables(conn)
        cur = conn.execute(
            "SELECT id, name, dataset_type, created_at, status, local_path, remote_uri, stats_json, manifest_json, error "
            "FROM training_datasets WHERE id = ?",
            (dataset_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_dataset(row)


def list_datasets(limit: int = 50) -> List[dict]:
    db = _db()
    with db._connect() as conn:
        init_training_tables(conn)
        cur = conn.execute(
            "SELECT id, name, dataset_type, created_at, status, local_path, remote_uri, stats_json, manifest_json, error "
            "FROM training_datasets ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
    return [_row_dataset(r) for r in rows]


def delete_dataset(dataset_id: str) -> bool:
    db = _db()
    with db._connect() as conn:
        init_training_tables(conn)
        cur = conn.execute("DELETE FROM training_datasets WHERE id = ?", (dataset_id,))
        conn.commit()
        return cur.rowcount > 0


def save_job(rec: dict) -> dict:
    db = _db()
    now = time.time()
    rec = {**rec, "created_at": rec.get("created_at") or now}
    with db._connect() as conn:
        init_training_tables(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO training_jobs (
                id, dataset_id, job_type, status, base_model, created_at, started_at, finished_at,
                output_path, cloud_job_id, log_text, error, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec["id"],
                rec["dataset_id"],
                rec.get("job_type") or "yolo_local",
                rec.get("status") or "pending",
                rec.get("base_model"),
                rec["created_at"],
                rec.get("started_at"),
                rec.get("finished_at"),
                rec.get("output_path"),
                rec.get("cloud_job_id"),
                rec.get("log_text"),
                rec.get("error"),
                json.dumps(rec.get("metrics") or {}, default=str) if rec.get("metrics") else None,
            ),
        )
        conn.commit()
    return rec


def update_job(job_id: str, **fields) -> Optional[dict]:
    job = get_job(job_id)
    if not job:
        return None
    merged = {**job, **fields}
    save_job(merged)
    return get_job(job_id)


def get_job(job_id: str) -> Optional[dict]:
    db = _db()
    with db._connect() as conn:
        init_training_tables(conn)
        cur = conn.execute(
            """
            SELECT id, dataset_id, job_type, status, base_model, created_at, started_at, finished_at,
                   output_path, cloud_job_id, log_text, error, metrics_json
            FROM training_jobs WHERE id = ?
            """,
            (job_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_job(row)


def list_jobs(limit: int = 30) -> List[dict]:
    db = _db()
    with db._connect() as conn:
        init_training_tables(conn)
        cur = conn.execute(
            """
            SELECT id, dataset_id, job_type, status, base_model, created_at, started_at, finished_at,
                   output_path, cloud_job_id, log_text, error, metrics_json
            FROM training_jobs ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [_row_job(r) for r in rows]


def _row_dataset(row) -> dict:
    try:
        stats = json.loads(row[7]) if row[7] else {}
    except json.JSONDecodeError:
        stats = {}
    try:
        manifest = json.loads(row[8]) if row[8] else {}
    except json.JSONDecodeError:
        manifest = {}
    return {
        "id": row[0],
        "name": row[1],
        "dataset_type": row[2],
        "created_at": row[3],
        "status": row[4],
        "local_path": row[5],
        "remote_uri": row[6],
        "stats": stats,
        "manifest": manifest,
        "error": row[9],
    }


def _row_job(row) -> dict:
    try:
        metrics = json.loads(row[12]) if row[12] else {}
    except json.JSONDecodeError:
        metrics = {}
    return {
        "id": row[0],
        "dataset_id": row[1],
        "job_type": row[2],
        "status": row[3],
        "base_model": row[4],
        "created_at": row[5],
        "started_at": row[6],
        "finished_at": row[7],
        "output_path": row[8],
        "cloud_job_id": row[9],
        "log_text": row[10],
        "error": row[11],
        "metrics": metrics,
    }
