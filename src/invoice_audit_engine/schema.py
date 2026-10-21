"""
Audit Data Schemas and Mathematical Reconciliation Models for Invoices and Receipts.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    calculated_total: Optional[float] = None
    line_variance: Optional[float] = None
    is_math_sound: bool = True
    is_quantity_inferred: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    raw_text: Optional[str] = None

class InvoiceHeader(BaseModel):
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    tax_id: Optional[str] = None          # TRN / VAT / GST / NTN
    po_number: Optional[str] = None
    document_type: str = "INVOICE"        # "INVOICE" or "RECEIPT"
    currency: Optional[str] = "USD"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class InvoiceTotals(BaseModel):
    subtotal: Optional[float] = None
    discount: Optional[float] = 0.0
    tax_amount: Optional[float] = None
    tax_rate_pct: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = "USD"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class AuditValidationFlags(BaseModel):
    is_mathematically_sound: bool = True
    math_mismatch_totals: bool = False
    totals_discrepancy_amount: float = 0.0
    math_mismatch_line_items: bool = False
    lines_discrepancy_amount: float = 0.0
    line_item_calc_mismatches_count: int = 0
    missing_tax_id_on_taxed_invoice: bool = False
    low_ocr_confidence: bool = False
    notes: List[Optional[str]] = Field(default_factory=list)

class InvoiceExtractionResult(BaseModel):
    file_path: str
    file_hash_sha256: str
    document_type: str = "INVOICE"
    is_invoice_archetype: bool = True
    exclusion_reason: Optional[str] = None
    quality_passed: bool
    quality_metrics: Dict[str, Any]
    header: Optional[InvoiceHeader] = None
    line_items: List[LineItem] = Field(default_factory=list)
    totals: Optional[InvoiceTotals] = None
    audit_flags: AuditValidationFlags = Field(default_factory=AuditValidationFlags)
    raw_token_count: int = 0
    average_ocr_confidence: float = 0.0
