"""
Two-Stage Quality Gate for Automated Audit Ingestion.

1. Stage 1 (Optical): Normalized sharpness and dynamic contrast range.
2. Stage 2 (Readability): Extracted token volume, confidence, and financial anchors.
"""

import cv2
import numpy as np
from pydantic import BaseModel
from typing import List, Tuple

class QualityReport(BaseModel):
    passed: bool
    resolution: Tuple[int, int]
    sharpness_score: float
    contrast_std: float
    issues: List[str]

def evaluate_image_quality(
    img: np.ndarray,
    min_width: int = 500,
    min_height: int = 500,
    min_sharpness: float = 30.0,
    min_contrast: float = 20.0
) -> QualityReport:
    """
    Evaluates optical readability before OCR using scale-normalized metrics.
    """
    if img is None or img.size == 0:
        return QualityReport(
            passed=False,
            resolution=(0, 0),
            sharpness_score=0.0,
            contrast_std=0.0,
            issues=["Failed to decode image or image buffer is empty."]
        )

    h, w = img.shape[:2]
    issues = []

    is_narrow_receipt = (h / max(w, 1)) >= 1.8
    effective_min_w = 300 if is_narrow_receipt else min_width
    if w < effective_min_w or h < min_height:
        issues.append(f"Resolution too low ({w}x{h}). Minimum required: {effective_min_w}x{min_height}.")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    # Scale-normalize to standard width of 1000px for scale-invariant sharpness calculation
    target_w = 1000
    scale = target_w / float(w)
    target_h = int(h * scale)
    normalized_gray = cv2.resize(gray, (target_w, max(target_h, 1)), interpolation=cv2.INTER_AREA)

    laplacian = cv2.Laplacian(normalized_gray, cv2.CV_64F)
    sharpness = float(laplacian.var())
    if sharpness < min_sharpness:
        issues.append(
            f"Image is out of focus or blurry (sharpness score {sharpness:.1f} < threshold {min_sharpness:.1f})."
        )

    contrast = float(gray.std())
    dynamic_range = float(gray.max() - gray.min()) if gray.size > 0 else 0.0
    # Sparse digital documents with razor-sharp text (sharpness >= 2x threshold) and full dynamic range (>= 150)
    # have lower global std due to whitespace, not washed-out scanning.
    is_crisp_sparse_doc = (sharpness >= min_sharpness * 2.0) and (dynamic_range >= 150.0)

    if contrast < min_contrast and not is_crisp_sparse_doc:
        issues.append(
            f"Low contrast or washed-out scan (contrast std {contrast:.1f} < threshold {min_contrast:.1f})."
        )

    return QualityReport(
        passed=len(issues) == 0,
        resolution=(w, h),
        sharpness_score=round(sharpness, 2),
        contrast_std=round(contrast, 2),
        issues=issues
    )

def evaluate_extraction_readability(
    token_count: int,
    avg_confidence: float,
    all_text: str,
    min_tokens: int = 8,
    min_confidence: float = 0.65
) -> Tuple[bool, List[str]]:
    """Evaluates whether extracted OCR tokens are sufficient for auditable processing."""
    issues = []
    if token_count < min_tokens:
        issues.append(
            f"Insufficient text extracted ({token_count} tokens < minimum {min_tokens}). "
            "Document is unreadable, cropped, or illegible. Flagged for clean rescan."
        )

    if avg_confidence < min_confidence:
        issues.append(
            f"OCR confidence too low ({avg_confidence * 100:.1f}% < threshold {min_confidence * 100:.1f}%). "
            "Faint print or severe compression detected. Flagged for clean rescan."
        )

    upper_txt = all_text.upper()
    has_anchor = any(kw in upper_txt for kw in ["TOTAL", "INVOICE", "AMOUNT", "TAX", "DATE", "BILL", "RECEIPT", "ORDER"])
    if not has_anchor:
        issues.append(
            "No core accounting anchors found (missing Total, Invoice, Amount, Tax, Date). "
            "Document layout unviable. Flagged for clean rescan."
        )

    return (len(issues) == 0, issues)
