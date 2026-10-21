"""
Image Normalization and Preprocessing for Document OCR.
"""

import cv2
import numpy as np
from typing import Tuple, List
import pypdfium2 as pdfium

def load_and_normalize_image(image_path: str) -> np.ndarray:
    """Loads image and normalizes to 3-channel BGR format, handling alpha channels."""
    img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Could not read image file at '{image_path}'")

    if len(img.shape) == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3] / 255.0
        bgr = img[:, :, :3]
        white_bg = np.ones_like(bgr, dtype=np.uint8) * 255
        img = (bgr * alpha[:, :, np.newaxis] + white_bg * (1 - alpha[:, :, np.newaxis])).astype(np.uint8)
    elif len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    return img

def load_document_pages(file_path: str) -> List[np.ndarray]:
    """
    Loads document pages as a list of BGR numpy arrays.
    Supports standard image formats (.png, .jpg, .tiff, .bmp) as well as multi-page .pdf files.
    """
    if file_path.lower().endswith(".pdf"):
        pdf = pdfium.PdfDocument(file_path)
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            # Render at 2.5x scale (approx 180-200 DPI) for crisp character definition
            bitmap = page.render(scale=2.5)
            pil_image = bitmap.to_pil()
            arr = np.array(pil_image)
            if len(arr.shape) == 3 and arr.shape[2] == 3:
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            elif len(arr.shape) == 3 and arr.shape[2] == 4:
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            elif len(arr.shape) == 2:
                bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            else:
                bgr = arr
            pages.append(bgr)
        return pages
    else:
        return [load_and_normalize_image(file_path)]

def normalize_illumination_and_shadows(img: np.ndarray, force: bool = False) -> np.ndarray:
    """
    Flattens uneven illumination, dark shadows, flash hot spots, and crumpled paper wrinkles
    using morphological background division while keeping high-frequency character strokes sharp.
    Only triggers if background brightness variance across the document exceeds threshold,
    preserving pristine digital scans and PDFs.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    if not force and h > 60 and w > 60:
        step_y, step_x = h // 3, w // 3
        grid_p90 = []
        for r in range(3):
            for c in range(3):
                block = gray[r * step_y:(r + 1) * step_y, c * step_x:(c + 1) * step_x]
                if block.size > 0:
                    grid_p90.append(float(np.percentile(block, 90)))
        illum_gradient = max(grid_p90) - min(grid_p90)
        if illum_gradient <= 35.0:
            return img

    kernel_size = max(25, int(min(h, w) * 0.05))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    background = np.maximum(background, 1)
    normalized = np.clip((gray.astype(np.float32) / background.astype(np.float32)) * 255.0, 0, 255).astype(np.uint8)
    return cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)

def deskew_image(img: np.ndarray, max_angle_threshold: float = 20.0) -> Tuple[np.ndarray, float]:
    """
    Corrects document skew using minimum area bounding rectangle of text pixel clusters.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    # Ignore outermost 5% margin to prevent scanner borders from skewing orientation
    h, w = img.shape[:2]
    margin_y = int(h * 0.05)
    margin_x = int(w * 0.05)
    inner_mask = thresh[margin_y:h - margin_y, margin_x:w - margin_x]

    y_coords, x_coords = np.where(inner_mask > 0)
    if len(y_coords) < 150:
        return img, 0.0

    # OpenCV minAreaRect expects (x, y) coordinates
    pts = np.column_stack([x_coords, y_coords]).astype(np.float32)
    rect = cv2.minAreaRect(pts)
    (cx, cy), (rect_w, rect_h), angle = rect

    # OpenCV returns angle between horizontal and first edge. Normalize to dominant text direction.
    if rect_w < rect_h:
        angle = angle + 90.0

    if angle > 90.0:
        angle -= 180.0
    elif angle < -90.0:
        angle += 180.0

    if abs(angle) < 0.6 or abs(angle) > max_angle_threshold:
        return img, 0.0

    center = (w // 2, h // 2)
    rot_mat = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        img, rot_mat, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255)
    )
    return rotated, angle

def enhance_contrast(img: np.ndarray) -> np.ndarray:
    """Enhances faint text using CLAHE on the L-channel in LAB color space."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_l = clahe.apply(l_channel)
    merged_lab = cv2.merge([enhanced_l, a_channel, b_channel])
    return cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)
