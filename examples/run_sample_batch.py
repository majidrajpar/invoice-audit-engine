"""
Quickstart Example: Running Invoice Audit Engine on Sample Documents.
"""

import os
import sys
from invoice_audit_engine import run_batch_audit

if __name__ == "__main__":
    # If a path was passed via CLI, use it; otherwise use package fixtures
    if len(sys.argv) > 1:
        target_dir = sys.argv[1]
    else:
        target_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures"))

    output_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "audit_sample_report.xlsx"))
    db_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sample_ledger.db"))

    print(f"Running Invoice Audit Engine on '{target_dir}'...")
    saved_path = run_batch_audit(
        input_paths=[target_dir],
        output_excel=output_file,
        db_path=db_file,
        max_workers=2
    )
    print(f"Audit completed. Report generated at: {saved_path}")
