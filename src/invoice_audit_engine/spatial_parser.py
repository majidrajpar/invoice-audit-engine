"""
2D Spatial Layout Parser and Extraction Engine for Invoices and Till Receipts.
"""

import re
import statistics
from typing import List, Optional
from dataclasses import dataclass
from .schema import InvoiceHeader, InvoiceTotals, LineItem, AuditValidationFlags

@dataclass
class OCRToken:
    text: str
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    confidence: float

    @property
    def center_x(self) -> float:
        return (self.xmin + self.xmax) / 2.0

    @property
    def center_y(self) -> float:
        return (self.ymin + self.ymax) / 2.0

    @property
    def height(self) -> float:
        return max(self.ymax - self.ymin, 1.0)

    @property
    def width(self) -> float:
        return max(self.xmax - self.xmin, 1.0)

def normalize_ocr_results(raw_ocr_results: list) -> List[OCRToken]:
    """Converts RapidOCR output into structured OCRToken list."""
    tokens = []
    if not raw_ocr_results:
        return tokens

    for item in raw_ocr_results:
        if not item or len(item) < 3:
            continue
        box, text, conf = item[0], item[1], item[2]
        try:
            conf_val = float(conf)
        except ValueError:
            conf_val = 0.5

        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        tokens.append(OCRToken(
            text=str(text).strip(),
            xmin=min(xs),
            ymin=min(ys),
            xmax=max(xs),
            ymax=max(ys),
            confidence=conf_val
        ))

    tokens.sort(key=lambda t: (t.ymin // 15, t.xmin))
    return tokens

CURRENCY_WORDS = re.compile(r"(?:AED|SAR|USD|EUR|GBP|DHS|SR|CAD|AUD|INR|CR|DR|[\$€£¥₹#])", re.IGNORECASE)

def clean_amount_str(raw_str: str) -> Optional[float]:
    """Cleans currency strings, handling negatives, accounting brackets (15.00), and CR flags."""
    if not raw_str:
        return None

    raw = raw_str.strip()

    # Reject percentages, dates, times, and non-numeric codes
    if "%" in raw or "/" in raw or raw.count("-") > 1 or ":" in raw:
        return None

    # Reject tokens containing non-currency words (e.g. '65W', 'CAT-6', 'M8x20')
    stripped_letters = CURRENCY_WORDS.sub("", raw)
    if re.search(r"[A-Za-z]", stripped_letters):
        return None

    is_negative = False
    if raw.startswith("(") and raw.endswith(")"):
        is_negative = True
        raw = raw[1:-1].strip()
    elif re.search(r"\b[Cc][Rr]\b$", raw):
        is_negative = True
        raw = re.sub(r"\b[Cc][Rr]\b$", "", raw).strip()
    elif raw.startswith("-"):
        is_negative = True
        raw = raw[1:].strip()
    elif raw.endswith("-"):
        is_negative = True
        raw = raw[:-1].strip()

    cleaned = re.sub(r"[^\d.,]", "", raw).strip()
    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." not in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) in [2, 3]:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    try:
        val = float(cleaned)
        if is_negative:
            val = -val
        return round(val, 2)
    except ValueError:
        return None

def is_monetary_token(txt: str) -> bool:
    """Determines whether a token exhibits monetary format (decimals, currency marker, or accounting sign)."""
    has_decimals = bool(re.search(r"\d+[.,]\d{2,3}", txt))
    has_currency = bool(re.search(r"[\$€£¥]|(?:AED|SAR|USD|EUR|GBP|DHS|SR)", txt, re.IGNORECASE))
    has_negative = (txt.startswith("(") and txt.endswith(")")) or txt.startswith("-") or txt.upper().endswith("CR")
    return has_decimals or has_currency or has_negative

class SpatialInvoiceParser:
    def __init__(self, tokens: List[OCRToken]):
        self.tokens = tokens
        self.all_text = " \n".join([t.text for t in tokens])
        
        # Spatial layout metrics (relative to document and font scale)
        if tokens:
            self.min_x = min(t.xmin for t in tokens)
            self.max_x = max(t.xmax for t in tokens)
            self.min_y = min(t.ymin for t in tokens)
            self.max_y = max(t.ymax for t in tokens)
            self.width_span = max(self.max_x - self.min_x, 1.0)
            self.height_span = max(self.max_y - self.min_y, 1.0)
            self.median_h = statistics.median([t.height for t in tokens])
        else:
            self.min_x = self.min_y = 0.0
            self.width_span = self.height_span = 1000.0
            self.median_h = 20.0

        self.document_type = self._detect_document_type()
        self.is_invoice_archetype = self.document_type in {"INVOICE", "RECEIPT", "CREDIT_NOTE"}
        self.exclusion_reason = (
            None if self.is_invoice_archetype
            else f"Document classified as non-invoice archetype: {self.document_type}"
        )

    def _is_same_row(self, t1: OCRToken, t2: OCRToken) -> bool:
        """Determines if two tokens sit on the same horizontal text baseline based on font height."""
        row_tol = max(t1.height, t2.height, self.median_h) * 0.70
        return abs(t1.center_y - t2.center_y) <= row_tol

    def _detect_document_type(self) -> str:
        if not self.tokens:
            return "NON_INVOICE_DOCUMENT"

        upper_txt = self.all_text.upper()

        # 1. Statement of Account / Bank Statement
        soa_patterns = [
            "STATEMENT OF ACCOUNT", "ACCOUNT STATEMENT", "BANK STATEMENT",
            "STATEMENT PERIOD", "OPENING BALANCE", "CLOSING BALANCE",
            "AGED ANALYSIS", "BALANCE BROUGHT FORWARD", "STATEMENT OF INVOICES"
        ]
        if any(kw in upper_txt for kw in soa_patterns):
            if "TAX INVOICE" not in upper_txt and "COMMERCIAL INVOICE" not in upper_txt:
                return "STATEMENT_OF_ACCOUNT"
            if "STATEMENT OF ACCOUNT" in upper_txt or "BANK STATEMENT" in upper_txt:
                return "STATEMENT_OF_ACCOUNT"

        # 2. Delivery Note / Goods Received / Packing Slip
        dn_patterns = [
            "DELIVERY NOTE", "DELIVERY CHALLAN", "PACKING SLIP", "PACKING LIST",
            "GOODS RECEIVED NOTE", "GOODS RECEIPT", "DISPATCH NOTE", "WAYBILL", "CONSIGNMENT NOTE"
        ]
        if any(kw in upper_txt for kw in dn_patterns):
            if "TAX INVOICE" not in upper_txt and "COMMERCIAL INVOICE" not in upper_txt:
                return "DELIVERY_NOTE"

        # 3. Remittance Advice / Payment Voucher
        remittance_patterns = [
            "REMITTANCE ADVICE", "PAYMENT ADVICE", "DISBURSEMENT VOUCHER", "PAYMENT VOUCHER"
        ]
        if any(kw in upper_txt for kw in remittance_patterns):
            return "REMITTANCE_ADVICE"

        # 4. Purchase Order
        po_patterns = ["PURCHASE ORDER", "PURCHASE REQUISITION"]
        if any(kw in upper_txt for kw in po_patterns):
            if not any(kw in upper_txt for kw in ["TAX INVOICE", "COMMERCIAL INVOICE", "INVOICE #", "INV #", "VAT INVOICE"]):
                return "PURCHASE_ORDER"

        # 5. Credit Note / Credit Memo
        if any(kw in upper_txt for kw in ["CREDIT NOTE", "CREDIT MEMO"]):
            return "CREDIT_NOTE"

        # 6. Thermal Till Receipt
        aspect_ratio = self.height_span / self.width_span
        receipt_indicators = ["CASH", "CHANGE", "TILL", "REG #", "ORDER #", "RECEIPT", "STORE #", "SUBTOTAL"]
        receipt_score = sum(1 for kw in receipt_indicators if kw in upper_txt)
        is_explicit_invoice = any(kw in upper_txt for kw in ["TAX INVOICE", "COMMERCIAL INVOICE", "INVOICE #", "INV #", "BILL TO"])
        if not is_explicit_invoice and (aspect_ratio >= 1.8 or receipt_score >= 3):
            return "RECEIPT"

        # 7. Standard Invoices
        invoice_indicators = [
            "INVOICE", "TAX INVOICE", "COMMERCIAL INVOICE", "BILL TO", "INVOICE #",
            "INV #", "VAT INVOICE", "TOTAL DUE", "AMOUNT DUE", "BALANCE DUE"
        ]
        if any(kw in upper_txt for kw in invoice_indicators):
            return "INVOICE"

        # 8. Unclassified non-invoice document
        return "NON_INVOICE_DOCUMENT"

    def extract_header(self) -> InvoiceHeader:
        inv_number = None
        inv_date = None
        due_date = None
        tax_id = None
        po_number = None
        currency = "USD"
        vendor_name = None

        if re.search(r"\b(AED|DHS)\b", self.all_text, re.IGNORECASE):
            currency = "AED"
        elif re.search(r"\b(SAR|SR)\b", self.all_text, re.IGNORECASE):
            currency = "SAR"
        elif re.search(r"\b(EUR|€)\b", self.all_text):
            currency = "EUR"
        elif re.search(r"\b(GBP|£)\b", self.all_text):
            currency = "GBP"
        elif "$" in self.all_text or "USD" in self.all_text:
            currency = "USD"

        date_pattern = re.compile(r"\b(\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b")
        inv_pattern = re.compile(r"\b(?:INVOICE|INV|BILL|ORDER)[-_#\s:]+([A-Za-z0-9\-_]{2,20})\b", re.IGNORECASE)
        tax_pattern = re.compile(r"\b(?:TRN|VAT|TAX\s*ID|GSTIN)[-_#\s:]*([A-Za-z0-9]{5,20})\b", re.IGNORECASE)

        for t in self.tokens:
            txt_upper = t.text.upper()

            if not inv_number and any(kw in txt_upper for kw in ["INVOICE#", "INVOICE #", "INV #", "INV#", "INVOICE NO", "INVOLCE NO", "INVOLCE", "INVOICENUMBER", "ORDER #", "RECEIPT #", "CHECK #", "INVOICE:"]):
                match = re.search(r"(?:#|NO|NUM|NUMBER)[:\s]*([A-Za-z0-9\-_]+)", t.text, re.IGNORECASE)
                if match and len(match.group(1)) > 1 and match.group(1).upper() not in {"INVOICE", "INVOLCE", "OICE", "VOICE", "TAX"}:
                    inv_number = match.group(1)
                else:
                    same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                    if same_row:
                        same_row.sort(key=lambda x: x.xmin)
                        cand_txt = re.sub(r"[^\w\-]", "", same_row[0].text)
                        if cand_txt.upper() not in {"INVOICE", "INVOLCE", "OICE", "VOICE", "TAX", "NO"}:
                            inv_number = cand_txt
                    else:
                        below = [
                            cand for cand in self.tokens
                            if 0 < (cand.ymin - t.ymax) <= (self.median_h * 2.5) and abs(cand.center_x - t.center_x) < (self.median_h * 6.0)
                        ]
                        if below:
                            below.sort(key=lambda x: x.ymin)
                            cand_txt = re.sub(r"[^\w\-]", "", below[0].text)
                            if cand_txt.upper() not in {"INVOICE", "INVOLCE", "OICE", "VOICE", "TAX", "NO", "DATE"}:
                                inv_number = cand_txt

            if not po_number and any(kw in txt_upper for kw in ["P.O.#", "PO #", "P.O. NO", "PURCHASE ORDER"]):
                same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                if same_row:
                    same_row.sort(key=lambda x: x.xmin)
                    po_number = re.sub(r"[^\w\-/]", "", same_row[0].text)

            if not inv_date and "DUE" not in txt_upper and any(kw in txt_upper for kw in ["INVOICEDATE", "INVOICE DATE", "INVOICE DOTE", "DATE:", "DATE", "DOTE"]):
                m = date_pattern.search(t.text)
                if m:
                    inv_date = m.group(1)
                else:
                    same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                    if same_row:
                        same_row.sort(key=lambda x: x.xmin)
                        m_adj = date_pattern.search(same_row[0].text)
                        if m_adj:
                            inv_date = m_adj.group(1)
                    else:
                        below = [
                            cand for cand in self.tokens
                            if 0 < (cand.ymin - t.ymax) <= (self.median_h * 2.5) and abs(cand.center_x - t.center_x) < (self.median_h * 6.0)
                        ]
                        if below:
                            below.sort(key=lambda x: x.ymin)
                            m_adj = date_pattern.search(below[0].text)
                            if m_adj:
                                inv_date = m_adj.group(1)

            if not due_date and any(kw in txt_upper for kw in ["DUE DATE", "PAYMENT DUE"]):
                m = date_pattern.search(t.text)
                if m:
                    due_date = m.group(1)
                else:
                    same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                    if same_row:
                        same_row.sort(key=lambda x: x.xmin)
                        m_adj = date_pattern.search(same_row[0].text)
                        if m_adj:
                            due_date = m_adj.group(1)
                    else:
                        below = [
                            cand for cand in self.tokens
                            if 0 < (cand.ymin - t.ymax) <= (self.median_h * 2.5) and abs(cand.center_x - t.center_x) < (self.median_h * 6.0)
                        ]
                        if below:
                            below.sort(key=lambda x: x.ymin)
                            m_adj = date_pattern.search(below[0].text)
                            if m_adj:
                                due_date = m_adj.group(1)

            if not tax_id and any(kw in txt_upper for kw in ["TRN", "VAT ID", "TAX ID", "VAT REG", "VAT NO", "GSTIN"]):
                match = tax_pattern.search(t.text)
                if match:
                    tax_id = match.group(1)
                else:
                    same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                    if same_row:
                        same_row.sort(key=lambda x: x.xmin)
                        tax_id = re.sub(r"[^\w]", "", same_row[0].text)

        if not inv_number:
            m = inv_pattern.search(self.all_text)
            if m:
                cand = m.group(1)
                if cand.upper() not in {"INVOICE", "INVOLCE", "OICE", "VOICE", "BILL", "ORDER", "TAX", "DATE"}:
                    inv_number = cand

        if inv_number and inv_number.upper() in {"INVOICE", "INVOLCE", "OICE", "VOICE", "TAX"}:
            inv_number = None

        if not inv_date:
            m = date_pattern.search(self.all_text)
            if m:
                inv_date = m.group(1)

        if not tax_id:
            m = tax_pattern.search(self.all_text)
            if m:
                tax_id = m.group(1)

        # Infer Vendor Name: highest prominent text block in top 28% of page, excluding document titles and form labels
        top_y_cutoff = self.min_y + (self.height_span * 0.28)
        GENERIC_TITLES = {
            "INVOICE", "TAXINVOICE", "TAX INVOICE", "CORPORATE INVOICE", "CORPORATEINVOICE",
            "COMMERCIAL INVOICE", "COMMERCIALINVOICE", "BILL", "RECEIPT", "CASH RECEIPT",
            "QUOTE", "PRICE QUOTE", "PURCHASE ORDER", "STATEMENT", "STATEMENT OF ACCOUNT",
            "SELLER NAME", "CUSTOMER NAME", "BILL TO", "SHIP TO", "REGISTERED", "ADDRESS",
            "DATE", "PHONE", "GSTIN", "TAX", "MANAGER", "ORIGINAL", "DUPLICATE"
        }
        top_tokens = []
        for t in self.tokens:
            if t.ymin < top_y_cutoff:
                clean_t = re.sub(r"[^A-Za-z0-9 ]", "", t.text).strip().upper()
                if clean_t and clean_t not in GENERIC_TITLES and len(clean_t) > 2:
                    top_tokens.append(t)
        if top_tokens:
            top_tokens.sort(key=lambda t: t.height, reverse=True)
            vendor_name = top_tokens[0].text

        return InvoiceHeader(
            vendor_name=vendor_name,
            invoice_number=inv_number,
            invoice_date=inv_date,
            due_date=due_date,
            tax_id=tax_id,
            po_number=po_number,
            document_type=self.document_type,
            currency=currency
        )

    def extract_totals(self) -> InvoiceTotals:
        if not self.is_invoice_archetype:
            return InvoiceTotals(
                subtotal=None,
                discount=0.0,
                tax_amount=None,
                tax_rate_pct=None,
                total_amount=None
            )

        subtotal = None
        tax_amount = None
        tax_rate_pct = None
        discount_amount = 0.0
        total_amount = None

        is_credit_note = any(kw in self.all_text.upper() for kw in ["CREDIT NOTE", "CREDIT MEMO", "REFUND", "ADJUSTMENT"])

        # 1. Search Subtotal
        for t in self.tokens:
            txt_upper = t.text.upper()
            if subtotal is None and any(kw in txt_upper for kw in ["SUBTOTAL", "SUB-TOTAL", "TOTAL BEFORE TAX", "TOTAL(BEFORE", "TAXABLE VALUE", "NET AMOUNT"]):
                m_sub = re.search(r"[:\s]+(\d[\d,]*(?:\.\d+)?)\b", t.text)
                if m_sub:
                    amt = clean_amount_str(m_sub.group(1))
                    if amt is not None:
                        subtotal = amt if is_credit_note else abs(amt)
                if subtotal is None:
                    same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                    for cand in sorted(same_row, key=lambda x: x.xmin):
                        amt = clean_amount_str(cand.text)
                        if amt is not None:
                            subtotal = amt if is_credit_note else abs(amt)
                            break
                if subtotal is None:
                    max_below_dist = self.median_h * 2.5
                    below = [
                        cand for cand in self.tokens 
                        if 0 < (cand.ymin - t.ymax) <= max_below_dist and abs(cand.center_x - t.center_x) < (self.median_h * 8.0)
                    ]
                    for cand in sorted(below, key=lambda x: x.ymin):
                        amt = clean_amount_str(cand.text)
                        if amt is not None:
                            subtotal = amt if is_credit_note else abs(amt)
                            break

            if any(kw in txt_upper for kw in ["DISCOUNT", "PROMO", "REBATE", "CREDIT"]) and "CARD" not in txt_upper:
                same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                for cand in sorted(same_row, key=lambda x: x.xmin):
                    amt = clean_amount_str(cand.text)
                    if amt is not None:
                        discount_amount = abs(amt)
                        break

            if tax_amount is None and any(kw in txt_upper for kw in ["TAX", "VAT", "GST", "IGST", "IOST", "CGST", "SGST", "TOTAL TAX", "TOTALTAX"]) and "INVOICE TOTAL" not in txt_upper and "TAX INVOICE" not in txt_upper:
                pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", t.text)
                if pct_match:
                    try:
                        tax_rate_pct = float(pct_match.group(1))
                    except ValueError:
                        pass
                same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                for cand in sorted(same_row, key=lambda x: x.xmin):
                    if "%" in cand.text:
                        continue
                    amt = clean_amount_str(cand.text)
                    if amt is not None:
                        tax_amount = abs(amt)
                        break

        # 2. Bottom-Up Grand Total Search: prioritize bottom summary block over table column headers
        sorted_bottom_up = sorted(self.tokens, key=lambda tok: tok.ymin, reverse=True)
        for t in sorted_bottom_up:
            if total_amount is not None:
                break
            txt_upper = t.text.upper()
            if any(kw in txt_upper for kw in [
                "TOTAL", "GRAND TOTAL", "CRANDTOTAL", "BALANCE DUE", "TOTAL AMOUNT", "TOTAL DUE", "AMOUNT DUE", "TOTAT", "TOTL"
            ]) and "SUB" not in txt_upper and "TAXABLE" not in txt_upper:
                m_tot = re.search(r"[:\s]+(\d[\d,]*(?:\.\d+)?)\b", t.text)
                if m_tot:
                    amt = clean_amount_str(m_tot.group(1))
                    if amt is not None:
                        total_amount = amt if is_credit_note else abs(amt)
                if total_amount is None:
                    same_row = [cand for cand in self.tokens if cand.xmin > t.xmin and self._is_same_row(t, cand)]
                    for cand in sorted(same_row, key=lambda x: x.xmin):
                        amt = clean_amount_str(cand.text)
                        if amt is not None:
                            total_amount = amt if is_credit_note else abs(amt)
                            break
                if total_amount is None:
                    max_below_dist = self.median_h * 2.8
                    below = [
                        cand for cand in self.tokens 
                        if 0 < (cand.ymin - t.ymax) <= max_below_dist and abs(cand.center_x - t.center_x) < (self.median_h * 8.0)
                    ]
                    for cand in sorted(below, key=lambda x: x.ymin):
                        amt = clean_amount_str(cand.text)
                        if amt is not None:
                            total_amount = amt if is_credit_note else abs(amt)
                            break

        return InvoiceTotals(
            subtotal=subtotal,
            discount=discount_amount,
            tax_amount=tax_amount,
            tax_rate_pct=tax_rate_pct,
            total_amount=total_amount
        )

    def extract_line_items(self) -> List[LineItem]:
        if not self.is_invoice_archetype:
            return []

        items = []

        if self.document_type == "RECEIPT":
            header_y = self.min_y + (self.height_span * 0.12)
            footer_y = 99999.0
            for t in self.tokens:
                txt_upper = t.text.upper()
                if any(kw in txt_upper for kw in ["SUBTOTAL", "TOTAL", "CASH", "VISA", "CARD", "CHANGE"]):
                    if t.ymin > header_y and t.ymin < footer_y:
                        footer_y = t.ymin
        else:
            header_y = None
            max_header_y = self.min_y + (self.height_span * 0.60)
            for t in self.tokens:
                txt_upper = t.text.upper()
                if any(kw in txt_upper for kw in ["DESCRIPTION", "ITEM", "DETAILS"]) and t.ymin < max_header_y:
                    header_y = t.ymax
                    break

            if not header_y:
                header_y = self.min_y + (self.height_span * 0.28)

            footer_y = 99999.0
            for t in self.tokens:
                txt_upper = t.text.upper()
                is_header_col = any(h in txt_upper for h in ["ITEM", "DESCRIPTION", "QTY", "RATE", "UNIT", "PRICE"])
                if is_header_col:
                    continue
                is_footer_kw = (
                    any(kw in txt_upper for kw in [
                        "SUBTOTAL", "TOTAL AMOUNT", "GRAND TOTAL", "CRANDTOTAL", "BALANCE DUE", "AMOUNT DUE",
                        "PAYMENT TERMS", "TERMS &", "TOTALTAX", "TOTAL TAX", "TAX)", "TOTAL(",
                        "IGST", "CGST", "SGST", "SALES TAX", "AMOUNT PAID", "BANK TRANSFER"
                    ])
                    or (txt_upper.strip() in {"TOTAL", "TOTAL:", "TOTAT", "TOTAT:"})
                )
                if is_footer_kw and t.ymin > (header_y + self.median_h):
                    if t.ymin < footer_y:
                        footer_y = t.ymin

        table_tokens = [t for t in self.tokens if header_y <= t.ymin < footer_y]
        if not table_tokens:
            return items

        # Cluster by horizontal lines using adaptive font height threshold
        line_cluster_tolerance = self.median_h * 0.75
        lines = []
        current_line = []
        table_tokens.sort(key=lambda t: t.ymin)

        for t in table_tokens:
            if not current_line:
                current_line.append(t)
            else:
                if abs(t.ymin - current_line[0].ymin) <= line_cluster_tolerance:
                    current_line.append(t)
                else:
                    lines.append(sorted(current_line, key=lambda x: x.xmin))
                    current_line = [t]
        if current_line:
            lines.append(sorted(current_line, key=lambda x: x.xmin))

        # Multi-Column Lane Discovery:
        # Detect physical column centers to isolate Description, Qty, Unit Price, and Line Total.
        header_lane_tokens = [t for t in self.tokens if abs(t.ymin - header_y) <= (self.median_h * 2.2)]
        detected_lanes = {}
        for ht in header_lane_tokens:
            txt_u = ht.text.upper()
            if any(k in txt_u for k in ["QTY", "QUANTITY"]):
                detected_lanes["qty"] = ht.center_x
            elif any(k in txt_u for k in ["RATE", "PRICE", "UNIT"]):
                detected_lanes["rate"] = ht.center_x
            elif any(k in txt_u for k in ["TOTAL", "AMOUNT"]) and "SUBTOTAL" not in txt_u:
                detected_lanes["total"] = ht.center_x

        if "total" not in detected_lanes:
            rightmost_amts = []
            for l in lines:
                m_tokens = [t for t in l if clean_amount_str(t.text) is not None and is_monetary_token(t.text)]
                if m_tokens:
                    rightmost_amts.append(m_tokens[-1].center_x)
            if rightmost_amts:
                detected_lanes["total"] = statistics.median(rightmost_amts)

        gutter_tol = self.width_span * 0.12

        for line in lines:
            line_texts = [tok.text for tok in line]
            full_line_str = " ".join(line_texts)
            full_line_upper = full_line_str.upper()

            # Hard termination barrier: stop if summary or tax footer row is encountered
            if any(kw in full_line_upper for kw in [
                "SUBTOTAL", "GRAND TOTAL", "CRANDTOTAL", "BALANCE DUE", "TOTALTAX", "TOTAL TAX",
                "TOTAL(BEFORE", "TOTAL (BEFORE", "TAX)", "IGST", "CGST", "SGST", "SALES TAX",
                "AMOUNT PAID", "BANK TRANSFER", "PAYMENT INSTRUCTION"
            ]):
                break

            # Identify monetary amounts on the line
            monetary_indices = [
                i for i, tok in enumerate(line)
                if clean_amount_str(tok.text) is not None and is_monetary_token(tok.text)
            ]
            if not monetary_indices:
                if line and clean_amount_str(line[-1].text) is not None:
                    monetary_indices = [len(line) - 1]
                else:
                    # Check if monetary figures are embedded in a single text token
                    amt_matches = list(re.finditer(r"\b\d[\d,]*(?:\.\d{2})\b", full_line_str))
                    if not amt_matches:
                        continue
                    if len(amt_matches) >= 2:
                        u_price = clean_amount_str(amt_matches[-2].group(0))
                        l_total = clean_amount_str(amt_matches[-1].group(0))
                        pre_str = full_line_str[:amt_matches[-2].start()].strip()
                    else:
                        u_price = None
                        l_total = clean_amount_str(amt_matches[-1].group(0))
                        pre_str = full_line_str[:amt_matches[-1].start()].strip()

                    m_qty = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:hrs?|hours?|pcs?|units?|items?|qty|x|ea)?\s*$", pre_str, re.IGNORECASE)
                    l_qty = None
                    is_q_inferred = False
                    if m_qty:
                        try:
                            l_qty = float(m_qty.group(1))
                            l_desc = pre_str[:m_qty.start()].strip() or "Item"
                        except ValueError:
                            l_desc = pre_str or "Item"
                    else:
                        l_desc = pre_str or "Item"

                    if l_qty is None and u_price is not None and l_total is not None and u_price > 0:
                        ratio = l_total / u_price
                        if abs(ratio - round(ratio)) < 0.01:
                            l_qty = float(round(ratio))
                            is_q_inferred = True

                    c_total = round(l_qty * u_price, 2) if (l_qty is not None and u_price is not None) else None
                    l_var = round(abs(l_total - c_total), 2) if (c_total is not None and l_total is not None) else None
                    m_sound = not (not is_q_inferred and l_var is not None and l_var > 0.05)

                    avg_conf = sum(tok.confidence for tok in line) / len(line)
                    items.append(LineItem(
                        description=l_desc,
                        quantity=l_qty,
                        unit_price=u_price,
                        line_total=l_total,
                        calculated_total=c_total,
                        line_variance=l_var,
                        is_math_sound=m_sound,
                        is_quantity_inferred=is_q_inferred,
                        confidence=round(avg_conf, 2),
                        raw_text=full_line_str
                    ))
                    continue

            line_total = None
            unit_price = None
            first_amt_idx = monetary_indices[-1]

            # Use detected column lanes if available
            if "total" in detected_lanes:
                tot_candidates = [i for i in monetary_indices if abs(line[i].center_x - detected_lanes["total"]) <= gutter_tol]
                if tot_candidates:
                    line_total = clean_amount_str(line[tot_candidates[-1]].text)
                    first_amt_idx = tot_candidates[0]

            if "rate" in detected_lanes:
                rate_candidates = [i for i in monetary_indices if abs(line[i].center_x - detected_lanes["rate"]) <= gutter_tol]
                if rate_candidates:
                    unit_price = clean_amount_str(line[rate_candidates[0]].text)
                    first_amt_idx = min(first_amt_idx, rate_candidates[0])

            # Fallback to positional monetary heuristics if lanes didn't resolve both
            if line_total is None or (unit_price is None and len(monetary_indices) >= 2):
                if len(monetary_indices) >= 2:
                    unit_price = clean_amount_str(line[monetary_indices[-2]].text)
                    line_total = clean_amount_str(line[monetary_indices[-1]].text)
                    first_amt_idx = monetary_indices[-2]
                else:
                    line_total = clean_amount_str(line[monetary_indices[-1]].text)
                    first_amt_idx = monetary_indices[-1]

            pre_tokens = line[:first_amt_idx]
            qty = None
            qty_tok_idx = None

            # 1. Lane-based quantity detection
            if "qty" in detected_lanes and pre_tokens:
                for i, tok in enumerate(pre_tokens):
                    if abs(tok.center_x - detected_lanes["qty"]) <= gutter_tol:
                        cleaned_q = tok.text.strip().replace(",", "")
                        if cleaned_q.replace(".", "", 1).isdigit():
                            qty = float(cleaned_q)
                            qty_tok_idx = i
                            break

            # 2. Standalone numeric token immediately preceding price columns
            if qty is None and pre_tokens:
                last_pre = pre_tokens[-1].text.strip()
                if last_pre.isdigit() and int(last_pre) < 10000:
                    qty = float(last_pre)
                    qty_tok_idx = len(pre_tokens) - 1

            # 3. Embedded quantity with unit inside description tokens
            if qty is None and pre_tokens:
                full_pre = " ".join(t.text for t in pre_tokens)
                m_qty = re.search(
                    r"\b(\d+(?:\.\d+)?)\s*(?:hrs?|hours?|pcs?|units?|items?|qty|x|ea)\b",
                    full_pre,
                    re.IGNORECASE
                )
                if m_qty:
                    qty = float(m_qty.group(1))

            # 3. Inferred quantity when unit_price & line_total are clean multiples
            is_qty_inferred = False
            if qty is None and unit_price is not None and line_total is not None and unit_price > 0:
                ratio = line_total / unit_price
                if abs(ratio - round(ratio)) < 0.01:
                    qty = float(round(ratio))
                    is_qty_inferred = True

            desc_tokens = [tok.text for i, tok in enumerate(pre_tokens) if i != qty_tok_idx]
            description = " ".join(desc_tokens).strip() or "Item"

            calc_total = None
            line_var = None
            is_math_sound = True

            if qty is not None and unit_price is not None and line_total is not None:
                calc_total = round(qty * unit_price, 2)
                line_var = round(abs(line_total - calc_total), 2)
                # Only flag math mismatch if quantity was explicitly present, avoiding circular logic
                if not is_qty_inferred and line_var > 0.05:
                    is_math_sound = False

            avg_conf = sum(tok.confidence for tok in line) / len(line)

            items.append(LineItem(
                description=description,
                quantity=qty,
                unit_price=unit_price,
                line_total=line_total,
                calculated_total=calc_total,
                line_variance=line_var,
                is_math_sound=is_math_sound,
                is_quantity_inferred=is_qty_inferred,
                confidence=round(avg_conf, 2),
                raw_text=full_line_str
            ))

        return items

    def reconcile_and_audit(
        self,
        header: InvoiceHeader,
        totals: InvoiceTotals,
        line_items: List[LineItem]
    ) -> AuditValidationFlags:
        flags = AuditValidationFlags()

        if not self.is_invoice_archetype:
            flags.is_mathematically_sound = True
            flags.notes.append(
                f"EXCLUDED_FROM_AUDIT: Document classified as '{self.document_type}'. Excluded from AP billing calculations."
            )
            return flags

        # Check 1: Totals Reconciliation (Subtotal - Discount + Tax == Total)
        if totals.subtotal is not None and totals.total_amount is not None:
            tax = totals.tax_amount or 0.0
            discount = totals.discount or 0.0
            expected_total = round(totals.subtotal - discount + tax, 2)
            diff = round(abs(expected_total - totals.total_amount), 2)

            if diff > 0.05:
                flags.math_mismatch_totals = True
                flags.totals_discrepancy_amount = diff
                flags.is_mathematically_sound = False
                flags.notes.append(
                    f"Totals mismatch: Subtotal ({totals.subtotal}) - Discount ({discount}) + Tax ({tax}) != Total ({totals.total_amount}). Variance: {diff}"
                )

        # Check 2: Line items sum against Subtotal
        priced_items = [item for item in line_items if item.line_total is not None]
        if priced_items and totals.subtotal is not None:
            line_sum = round(sum(item.line_total for item in priced_items), 2)
            diff_lines = round(abs(line_sum - totals.subtotal), 2)
            if diff_lines > 0.10:
                flags.math_mismatch_line_items = True
                flags.lines_discrepancy_amount = diff_lines
                flags.is_mathematically_sound = False
                flags.notes.append(
                    f"Line items sum ({line_sum}) does not match subtotal ({totals.subtotal}). Variance: {diff_lines}"
                )
        elif not priced_items and totals.subtotal is None and totals.total_amount is None:
            flags.is_mathematically_sound = False
            flags.notes.append("Unreconciled: Missing subtotal and grand total anchors on document.")

        # Check 3: Line Item Arithmetic (Qty x Rate == Line Total)
        calc_mismatches = [item for item in line_items if not item.is_math_sound]
        if calc_mismatches:
            flags.line_item_calc_mismatches_count = len(calc_mismatches)
            flags.is_mathematically_sound = False
            for m in calc_mismatches[:2]:
                flags.notes.append(
                    f"Line math mismatch on '{m.description}': {m.quantity} @ {m.unit_price} = {m.calculated_total}, but billed {m.line_total} (Variance: {m.line_variance})"
                )

        # Check 4: Tax Compliance (Tax charged without TRN)
        if totals.tax_amount and totals.tax_amount > 0:
            if not header.tax_id:
                flags.missing_tax_id_on_taxed_invoice = True
                flags.notes.append(
                    f"Tax of {totals.tax_amount} charged but no Tax ID / TRN was detected on the document."
                )

        return flags
