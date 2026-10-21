"""
SQLite Checkpoint Ledger for 10k+ Scale Batch Processing.

Ensures idempotence, crash-resilience, and instant resumes:
- Records each processed document by SHA-256 fingerprint.
- If a 10,000-document run is interrupted, re-running skips completed items instantly.
"""

import sqlite3
import json
import os
from typing import List, Optional, Set
from .schema import InvoiceExtractionResult

class CheckpointLedger:
    def __init__(self, db_path: str = "audit_ledger.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS invoice_audit_ledger (
                    file_hash TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    document_type TEXT,
                    quality_passed INTEGER,
                    average_confidence REAL,
                    is_math_sound INTEGER,
                    result_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON invoice_audit_ledger(file_hash);")
            conn.commit()

    def get_processed_hashes(self) -> Set[str]:
        """Returns set of all SHA-256 hashes already audited."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_hash FROM invoice_audit_ledger;")
            return {row[0] for row in cursor.fetchall()}

    def get_result_by_hash(self, file_hash: str) -> Optional[InvoiceExtractionResult]:
        """Loads a single result by SHA-256 hash."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT result_json FROM invoice_audit_ledger WHERE file_hash = ?;", (file_hash,))
            row = cursor.fetchone()
            if row:
                try:
                    return InvoiceExtractionResult(**json.loads(row[0]))
                except Exception:
                    return None
        return None

    def save_result(self, result: InvoiceExtractionResult):
        """Persists extraction result to SQLite ledger."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO invoice_audit_ledger (
                    file_hash, file_path, document_type, quality_passed,
                    average_confidence, is_math_sound, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (
                result.file_hash_sha256,
                result.file_path,
                result.document_type,
                1 if result.quality_passed else 0,
                result.average_ocr_confidence,
                1 if result.audit_flags.is_mathematically_sound else 0,
                result.model_dump_json()
            ))
            conn.commit()

    def load_all_results(self) -> List[InvoiceExtractionResult]:
        """Loads all results from database for report generation."""
        results = []
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT result_json FROM invoice_audit_ledger ORDER BY created_at ASC;")
            for row in cursor.fetchall():
                try:
                    data = json.loads(row[0])
                    results.append(InvoiceExtractionResult(**data))
                except Exception:
                    continue
        return results

    def get_counts(self) -> dict:
        """Returns summary counts from the ledger."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*), 
                    SUM(CASE WHEN quality_passed = 1 THEN 1 ELSE 0 END), 
                    SUM(CASE WHEN quality_passed = 1 AND is_math_sound = 1 THEN 1 ELSE 0 END) 
                FROM invoice_audit_ledger;
            """)
            row = cursor.fetchone()
            return {
                "total": row[0] or 0,
                "passed": row[1] or 0,
                "math_sound": row[2] or 0
            }
