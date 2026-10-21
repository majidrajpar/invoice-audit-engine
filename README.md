# Invoice Audit Engine (Continuous Document Telemetry)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> Automated continuous controls monitoring and substantive testing for accounts payable, procurement, and tax compliance. Replaces retrospective 25-invoice samples with 100% population verification on-premise.

---

## 1. Executive Summary & Problem Solved

Traditional accounts payable audits rely on sampling: testing 25 to 50 invoices annually out of 50,000 transactions. This leaves **99.9% of company spend completely unexamined**, allowing minor calculation errors, unverified sales tax, and procurement price inflation to compound into millions in silent EBITDA leakage.

Commercial cloud OCR products introduce severe vulnerabilities:
1. **Confidentiality Breaches:** Uploading proprietary vendor pricing, supplier margins, and banking details to public cloud APIs violates corporate privacy policies and non-disclosure agreements.
2. **Probabilistic Hallucination:** When faced with blurry, low-contrast, or tilted scans, cloud models guess rather than defend, silently hallucinating digits into general ledgers.

**Invoice Audit Engine** runs **100% locally on-premise in Python**. It operates as an automated continuous audit gateway:
- Evaluates scan quality before processing (rejecting degraded scans to an audit rescan queue rather than guessing).
- Reconstructs document geometry (handling both standard corporate tax invoices and thermal till receipts).
- Tests mathematical integrity at three levels: $\text{Qty} \times \text{Rate} = \text{Line Total}$, $\sum \text{Lines} = \text{Subtotal}$, and $\text{Subtotal} - \text{Discount} + \text{Tax} = \text{Grand Total}$.
- Verifies statutory tax compliance (flagging sales tax / VAT billed without a valid Tax Registration Number / TRN).
- Detects procurement price variance across vendors for identical line items.
- Automatically routes extractions with $< 70\%$ confidence to an amber manual verification queue.
- Generates an executive-ready **5-Tab Excel Audit Register**.

---

## 2. Technical Architecture

```
[Raw Invoices / Receipts / PDFs (PDF, JPG, PNG, TIFF, BMP, WEBP)]
                    │
                    ▼
       1. Zero-Trust Quality Gate
          ├── Stage 1: Optical Metric (Resolution ≥ 500px, Contrast σ ≥ 20, Sharpness)
          └── Stage 2: Readability Metric (Token yield ≥ 8, Anchors present)
                    │
           ┌────────┴────────┐
           ▼                 ▼
     [FAIL: Reject]    [PASS: Normalize]
     Routes to Rescan  Auto-deskew (OpenCV) & CLAHE contrast boost
     Queue             
                             │
                             ▼
                 2. Local 2D Spatial OCR
                    RapidOCR ONNX Runtime (CPU/GPU, zero cloud APIs)
                    Extracts tokens, coordinates (x, y, w, h), and confidences
                             │
                             ▼
                 3. Layout & Archetype Classifier
                    ├── RECEIPT: Borderless narrow-strip extraction
                    └── INVOICE: 2D tabular boundary & header parsing
                             │
                             ▼
                 4. Three-Way Mathematical Audit
                    ├── Line Math: Quantity × Unit Price == Line Total
                    ├── Subtotal Math: Sum(Line Items) == Subtotal
                    ├── Totals Math: Subtotal - Discount + Tax == Grand Total
                    └── Tax Compliance: Flag Tax > 0 without valid TRN / VAT ID
                             │
                             ▼
                 5. Persistence & Deliverables
                    ├── SQLite Checkpoint Ledger (audit_ledger.db)
                    └── 5-Tab Excel Master Audit Register (.xlsx)
```

---

## 3. Five-Tab Master Audit Workbook

The engine exports findings into a professionally formatted Excel register with conditional auditor color coding (Red = Substantive Exception, Amber = Manual Triage $<70\%$):

| Tab | Title | Audit Objective |
| :--- | :--- | :--- |
| **Tab 1** | **`Executive_Summary`** | High-level batch KPIs: Total documents audited, pass/rejection rate, total spend audited, total tax billed, and exception counts. |
| **Tab 2** | **`Invoice_Register`** | Header ledger: Document type, Vendor, Inv #, PO #, Dates, Subtotal, Discount, Tax, Total, TRN, OCR Confidence, and SHA-256 fingerprint. |
| **Tab 3** | **`Line_Item_Detail`** | Itemized lines with automated $\text{Qty} \times \text{Rate}$ reconciliation and individual line confidence triage. |
| **Tab 4** | **`Price_Variance_Analytics`** | Procurement intelligence: aggregates unit prices by item description across all invoices, highlighting price inflation and inconsistent vendor rates. |
| **Tab 5** | **`Audit_Exceptions`** | Actionable exception queue categorizing math mismatches, tax without TRN, and quality rejections with recommended audit steps. |

---

## 4. Scale & Performance (10,000+ Invoices)

Engineered for 100% population verification across large general ledgers:

- **Parallel Processing Pool:** Multi-core execution via Python's `ProcessPoolExecutor`. An 8-core workstation processes 10,000 invoices in **~40 minutes** (~0.25s per invoice).
- **SQLite Checkpoint Ledger (`audit_ledger.db`):** Records completed extractions by SHA-256 hash. If an audit run is stopped at document 6,400, re-running skips processed files and **resumes instantly at 6,401**.
- **Memory Streaming:** Keeps RAM usage under 300 MB throughout the run.
- **Excel Lock Resilience:** If the destination Excel file is open in Microsoft Excel, the engine saves to a timestamped file rather than terminating with a `PermissionError`.

---

## 5. Installation & Deployment
 
### Option A: Zero-Install Standalone Binary (Windows x64)
For internal audit teams, CFO desks, or corporate environments where Python is restricted:
1. Download the precompiled standalone executable (`invoice-audit-standalone.exe` or `invoice-audit-v1.0.0-windows-x64.zip`) from [GitHub Releases](https://github.com/majidrajpar/invoice-audit-engine/releases).
2. **Double-Click or Drag-and-Drop:** Drag invoice images or folders directly onto the `.exe`, or launch it to follow interactive prompts.
3. **Command Prompt / PowerShell:** Run directly from terminal with full flags: `invoice-audit-standalone.exe -i ./invoices -o ./audit_report.xlsx`.
4. Zero Python installation, zero cloud connections, zero telemetry leaks, zero administrative rights required.

### Option B: Python Source Installation (Cross-Platform)
```bash
# Clone the repository
git clone https://github.com/majidrajpar/invoice-audit-engine.git
cd invoice-audit-engine

# Install dependencies
pip install -r requirements.txt

# Or install in editable mode
pip install -e .
```

---

## 6. Usage

### Command-Line Interface (CLI)

```bash
# Audit a folder of invoices using auto-detected CPU cores
invoice-audit --input /path/to/invoices --output audit_report.xlsx

# Audit multiple folders with 8 parallel worker processes and SQLite checkpoint ledger
invoice-audit -i ./invoices_2025 ./invoices_2026 -o ./master_audit.xlsx --db audit_ledger.db -w 8

# Display full MIT License terms and copyright
invoice-audit --license

# Check engine version
invoice-audit --version
```

#### CLI Parameters Reference

| Flag | Long Flag | Description | Default |
| :--- | :--- | :--- | :--- |
| `-i` | `--input` | One or more input directories or image/PDF file paths. | *Required* |
| `-o` | `--output` | Destination path for generated 5-tab Excel audit workbook. | `<input>/audit_report.xlsx` |
| | `--db` | SQLite checkpoint ledger path for pause/resume idempotency. | `None` (fresh run) |
| `-w` | `--workers` | Parallel worker processes. | Auto-detected CPU cores |
| | `--no-deskew` | Disables automated OpenCV image deskewing. | `False` (deskew enabled) |
| | `--license` | Displays complete MIT License and copyright terms. | `False` |
| `-v` | `--version` | Displays engine version and attribution. | |
| `-h` | `--help` | Displays command usage and arguments. | |

### Python API

```python
from invoice_audit_engine import process_document, run_batch_audit

# 1. Audit a single invoice document
result = process_document("invoice_sample.png")

if result.quality_passed:
    print(f"Invoice Number: {result.header.invoice_number}")
    print(f"Subtotal: {result.totals.subtotal}")
    print(f"Tax: {result.totals.tax_amount}")
    print(f"Grand Total: {result.totals.total_amount}")
    print(f"Math Sound: {result.audit_flags.is_mathematically_sound}")
    print(f"Audit Notes: {result.audit_flags.notes}")
else:
    print(f"Rejected: {result.audit_flags.notes}")

# 2. Run high-scale batch audit across folders
run_batch_audit(
    input_paths=["./data/raw/high_quality", "./data/raw/low_quality"],
    output_excel="audit_master_register.xlsx",
    max_workers=8
)
```

---

## 7. Quality Gate & Confidence Rules

- **Optical Gate:** Rejects images with resolution $< 500\text{px}$ or contrast standard deviation $< 20.0$.
- **Readability Gate:** Rejects documents yielding $< 8$ tokens or lacking core financial anchors (`TOTAL`, `INVOICE`, `AMOUNT`, `TAX`, `DATE`).
- **Confidence Threshold ($70\%$):** 
  - Token extractions $\ge 70.0\%$ are accepted as verified.
  - Extractions $< 70.0\%$ are highlighted in **amber** and routed to the manual verification queue, preventing automation theater and phantom accounting errors.

---

## 8. Running Tests

```bash
# Run test suite
pytest tests/ -v
```

---

## 9. Compiling Standalone Executable (Windows x64)

To build the air-gapped, zero-dependency `invoice-audit-standalone.exe` from source:

```bash
# Install PyInstaller
pip install pyinstaller

# Build single-file executable using bundled ONNX runtime and PDFium hooks
pyinstaller invoice-audit-onefile.spec --clean
```
The compiled binary will be generated under `dist/invoice-audit-standalone.exe`.

---

## 10. License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 11. Author & Thought Leadership

**Majid Mumtaz** (CIA, ACA, FCCA, GRCP, GRCA)  
Director of Internal Audit & Risk Advisory | Audit Committee Secretary  
*Transforming retrospective sample-based audit routines into continuous 100% telemetry.*

- **LinkedIn:** [linkedin.com/in/majid-m-4b097118](https://www.linkedin.com/in/majid-m-4b097118/)
- **Newsletter:** *The Audit Signal* (Continuous Controls Monitoring & Audit Analytics)
- **YouTube Channel:** [@AuditBitz](https://www.youtube.com/@AuditBitz)
- **Portfolio & Open Tools:** [majidrajpar.github.io/portfolio_my](https://majidrajpar.github.io/portfolio_my)
