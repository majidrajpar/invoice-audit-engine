"""
Production Pipeline & Batch Orchestrator with Multiprocessing & Checkpointing.
"""

import os
import hashlib
from typing import List, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed
from rapidocr_onnxruntime import RapidOCR
from tqdm import tqdm

from .quality_gate import evaluate_image_quality, evaluate_extraction_readability
from .preprocessor import (
    load_document_pages,
    deskew_image,
    enhance_contrast,
    normalize_illumination_and_shadows
)
from .spatial_parser import normalize_ocr_results, SpatialInvoiceParser
from .schema import InvoiceExtractionResult, AuditValidationFlags
from .checkpoint_db import CheckpointLedger
from .excel_exporter import export_audit_to_excel

_WORKER_OCR: Optional[RapidOCR] = None

def _get_worker_ocr() -> RapidOCR:
    global _WORKER_OCR
    if _WORKER_OCR is None:
        _WORKER_OCR = RapidOCR()
    return _WORKER_OCR

def process_document(image_path: str, deskew: bool = True) -> InvoiceExtractionResult:
    """Processes a single invoice, receipt, or multi-page PDF with defensible error handling."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"File not found: {image_path}")

    try:
        with open(image_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
    except Exception:
        file_hash = hashlib.sha256(f"UNHASHABLE_{image_path}".encode()).hexdigest()

    try:
        pages = load_document_pages(image_path)
    except Exception as e:
        flags = AuditValidationFlags(
            is_mathematically_sound=False,
            notes=[f"REJECTED_DECODE_FAILURE: Document file corrupted or unsupported format ({e})"]
        )
        return InvoiceExtractionResult(
            file_path=image_path,
            file_hash_sha256=file_hash,
            quality_passed=False,
            quality_metrics={"passed": False, "issues": [str(e)]},
            audit_flags=flags,
            raw_token_count=0,
            average_ocr_confidence=0.0
        )

    if not pages:
        flags = AuditValidationFlags(
            is_mathematically_sound=False,
            notes=["REJECTED_EMPTY_DOCUMENT: Document contains no renderable pages."]
        )
        return InvoiceExtractionResult(
            file_path=image_path,
            file_hash_sha256=file_hash,
            quality_passed=False,
            quality_metrics={"passed": False, "issues": ["No pages rendered"]},
            audit_flags=flags,
            raw_token_count=0,
            average_ocr_confidence=0.0
        )

    q_report = evaluate_image_quality(pages[0])

    if not q_report.passed:
        flags = AuditValidationFlags(
            is_mathematically_sound=False,
            notes=[f"REJECTED_AT_QUALITY_GATE: {issue}" for issue in q_report.issues]
        )
        return InvoiceExtractionResult(
            file_path=image_path,
            file_hash_sha256=file_hash,
            quality_passed=False,
            quality_metrics=q_report.model_dump(),
            audit_flags=flags,
            raw_token_count=0,
            average_ocr_confidence=0.0
        )

    tokens = []
    cumulative_y = 0.0
    try:
        ocr = _get_worker_ocr()
        for page_img in pages:
            p_img = page_img
            if deskew:
                p_img, _ = deskew_image(p_img)
            p_img = normalize_illumination_and_shadows(p_img)
            p_img = enhance_contrast(p_img)

            raw_results, _ = ocr(p_img)
            p_tokens = normalize_ocr_results(raw_results)
            for t in p_tokens:
                t.ymin += cumulative_y
                t.ymax += cumulative_y
                tokens.append(t)
            cumulative_y += p_img.shape[0]

        tokens.sort(key=lambda t: (t.ymin // 15, t.xmin))
    except Exception as e:
        flags = AuditValidationFlags(
            is_mathematically_sound=False,
            notes=[f"REJECTED_OCR_FAILURE: Optical engine failed on document ({e})"]
        )
        return InvoiceExtractionResult(
            file_path=image_path,
            file_hash_sha256=file_hash,
            quality_passed=False,
            quality_metrics=q_report.model_dump(),
            audit_flags=flags,
            raw_token_count=0,
            average_ocr_confidence=0.0
        )

    token_count = len(tokens)
    avg_conf = (
        round(sum(t.confidence for t in tokens) / token_count, 3)
        if token_count > 0
        else 0.0
    )
    all_text = " ".join(t.text for t in tokens)

    is_readable, readability_issues = evaluate_extraction_readability(
        token_count=token_count,
        avg_confidence=avg_conf,
        all_text=all_text
    )

    if not is_readable:
        q_metrics = q_report.model_dump()
        q_metrics["issues"].extend(readability_issues)
        flags = AuditValidationFlags(
            is_mathematically_sound=False,
            notes=[f"REJECTED_AT_READABILITY_GATE: {issue}" for issue in readability_issues]
        )
        return InvoiceExtractionResult(
            file_path=image_path,
            file_hash_sha256=file_hash,
            quality_passed=False,
            quality_metrics=q_metrics,
            audit_flags=flags,
            raw_token_count=token_count,
            average_ocr_confidence=avg_conf
        )

    parser = SpatialInvoiceParser(tokens)
    header = parser.extract_header()
    totals = parser.extract_totals()
    line_items = parser.extract_line_items()
    audit_flags = parser.reconcile_and_audit(header, totals, line_items)

    if parser.is_invoice_archetype and avg_conf < 0.70:
        audit_flags.low_ocr_confidence = True
        audit_flags.notes.append(f"Low average OCR confidence ({avg_conf * 100:.1f}%). Manual triage suggested.")

    return InvoiceExtractionResult(
        file_path=image_path,
        file_hash_sha256=file_hash,
        document_type=parser.document_type,
        is_invoice_archetype=parser.is_invoice_archetype,
        exclusion_reason=parser.exclusion_reason,
        quality_passed=True,
        quality_metrics=q_report.model_dump(),
        header=header,
        line_items=line_items,
        totals=totals,
        audit_flags=audit_flags,
        raw_token_count=token_count,
        average_ocr_confidence=avg_conf
    )

def run_batch_audit(
    input_paths: List[str],
    output_excel: Optional[str] = None,
    db_path: Optional[str] = None,
    max_workers: Optional[int] = None,
    deskew: bool = True
) -> str:
    """
    Executes batch audit of documents strictly scoped to input targets.
    Generates Excel workbook directly in the target folder by default.
    """
    if not output_excel or output_excel == "audit_report.xlsx":
        if input_paths:
            primary = os.path.abspath(input_paths[0])
            base_dir = primary if os.path.isdir(primary) else os.path.dirname(primary)
            output_excel = os.path.join(base_dir, "audit_report.xlsx")
        else:
            output_excel = "audit_report.xlsx"

    ledger = CheckpointLedger(db_path) if db_path else None
    processed_hashes = ledger.get_processed_hashes() if ledger else set()

    # Discover files
    files_to_process = []
    supported_extensions = ('.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.pdf', '.webp', '.jfif')
    for path in input_paths:
        if os.path.isfile(path) and path.lower().endswith(supported_extensions):
            files_to_process.append(path)
        elif os.path.isdir(path):
            for root, _, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(supported_extensions):
                        files_to_process.append(os.path.join(root, f))

    files_to_process = sorted(list(set(files_to_process)))
    total_found = len(files_to_process)
    print(f"\nDiscovered {total_found} document files (images & PDFs) across inputs.")

    batch_results: List[InvoiceExtractionResult] = []
    pending_files = []

    for fpath in files_to_process:
        if ledger:
            try:
                with open(fpath, "rb") as f:
                    h = hashlib.sha256(f.read()).hexdigest()
                if h in processed_hashes:
                    cached_res = ledger.get_result_by_hash(h)
                    if cached_res:
                        batch_results.append(cached_res)
                        continue
            except Exception:
                pass
        pending_files.append(fpath)

    already_done = total_found - len(pending_files)
    if already_done > 0:
        print(f"Skipping {already_done} already-audited documents (loaded from SQLite ledger).")

    if pending_files:
        workers = max_workers or min(os.cpu_count() or 4, 8)
        print(f"Launching parallel execution pool ({workers} workers) for {len(pending_files)} pending documents...")

        if workers > 1 and len(pending_files) > 1:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(process_document, p, deskew): p for p in pending_files}
                for future in tqdm(as_completed(futures), total=len(futures), desc="Auditing Invoices"):
                    try:
                        res = future.result()
                        batch_results.append(res)
                        if ledger:
                            ledger.save_result(res)
                    except Exception as e:
                        p = futures[future]
                        print(f"Error processing {p}: {e}")
                        safe_err_hash = hashlib.sha256(f"WORKER_CRASH_{p}".encode()).hexdigest()
                        err_res = InvoiceExtractionResult(
                            file_path=p,
                            file_hash_sha256=safe_err_hash,
                            quality_passed=False,
                            quality_metrics={"passed": False, "issues": [str(e)]},
                            audit_flags=AuditValidationFlags(
                                is_mathematically_sound=False,
                                notes=[f"WORKER_CRASH_EXCEPTION: {e}"]
                            ),
                            raw_token_count=0,
                            average_ocr_confidence=0.0
                        )
                        batch_results.append(err_res)
                        if ledger:
                            ledger.save_result(err_res)
        else:
            for p in tqdm(pending_files, desc="Auditing Invoices"):
                try:
                    res = process_document(p, deskew)
                    batch_results.append(res)
                    if ledger:
                        ledger.save_result(res)
                except Exception as e:
                    print(f"Error processing {p}: {e}")
                    safe_err_hash = hashlib.sha256(f"WORKER_CRASH_{p}".encode()).hexdigest()
                    err_res = InvoiceExtractionResult(
                        file_path=p,
                        file_hash_sha256=safe_err_hash,
                        quality_passed=False,
                        quality_metrics={"passed": False, "issues": [str(e)]},
                        audit_flags=AuditValidationFlags(
                            is_mathematically_sound=False,
                            notes=[f"WORKER_CRASH_EXCEPTION: {e}"]
                        ),
                        raw_token_count=0,
                        average_ocr_confidence=0.0
                    )
                    batch_results.append(err_res)
                    if ledger:
                        ledger.save_result(err_res)

    batch_results.sort(key=lambda r: r.file_path)

    # Generate complete Excel workbook strictly for this batch
    print(f"\nGenerating master audit workbook with {len(batch_results)} total population records...")
    final_path = export_audit_to_excel(batch_results, output_excel)

    total_docs = len(batch_results)
    passed_docs = sum(1 for r in batch_results if r.quality_passed and r.is_invoice_archetype)
    rejected_docs = sum(1 for r in batch_results if not r.quality_passed)
    excluded_docs = sum(1 for r in batch_results if not r.is_invoice_archetype)
    reconciled_docs = sum(
        1 for r in batch_results
        if r.quality_passed and r.is_invoice_archetype and r.audit_flags and r.audit_flags.is_mathematically_sound
    )

    print("\n================ BATCH TELEMETRY SUMMARY ================")
    print(f"Total Population Ingested : {total_docs}")
    print(f"Clean (Passed Gate)       : {passed_docs}")
    if excluded_docs > 0:
        print(f"Non-Invoice Excluded      : {excluded_docs}")
    print(f"Rejected at Ingestion     : {rejected_docs}")
    print(f"Mathematically Reconciled : {reconciled_docs}")
    print(f"Master Audit Workbook     : {final_path}")
    print("=========================================================\n")

    return final_path
