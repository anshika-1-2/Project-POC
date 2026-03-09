"""
storage.py
Persist scan records to a local CSV file.

Saved fields per scan:
    user_id, user_name, user_allergen,
    dri_sex, dri_age, dri_height_cm, dri_weight_kg, dri_activity,
    dri_bmi, dri_eer_kcal,
    image_path, ingredients, detected_allergens, detected_additives, timestamp
"""

import csv
import uuid
import shutil
from datetime import datetime
from pathlib import Path

RECORDS_FILE = Path(__file__).parent.parent / "data" / "scan_records.csv"
IMAGES_DIR   = Path(__file__).parent.parent / "data" / "images"

FIELDNAMES = [
    "user_id",
    "user_name",
    "user_allergen",
    # DRI profile
    "dri_sex",
    "dri_age",
    "dri_height_cm",
    "dri_weight_kg",
    "dri_activity",
    "dri_bmi",
    "dri_eer_kcal",
    # Scan
    "image_path",
    "ingredients",
    "detected_allergens",
    "detected_additives",
    "timestamp",
]


def _ensure_files():
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    if not RECORDS_FILE.exists():
        with open(RECORDS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def save_image(source_path: str) -> str:
    """Copy uploaded image into data/images/ with a unique name. Returns saved path."""
    _ensure_files()
    ext      = Path(source_path).suffix or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest     = IMAGES_DIR / filename
    shutil.copy2(source_path, dest)
    return str(dest)


def save_record(
    user_id: str,
    user_name: str,
    user_allergen: str,
    image_path: str,
    ingredients: str,
    detected_allergens: list[str],
    detected_additives: list[tuple[int, str]],
    dri: dict | None = None,         # full dict from calculate_dri()
) -> str:
    """
    Append one scan record to the CSV.
    Returns the user_id used.
    """
    _ensure_files()
    if not user_id:
        user_id = uuid.uuid4().hex[:8].upper()

    allergens_str = "; ".join(detected_allergens)
    additives_str = "; ".join(f"{aid}:{name}" for aid, name in detected_additives)

    dri_inputs = (dri or {}).get("inputs") or {}
    row = {
        "user_id":            user_id,
        "user_name":          user_name,
        "user_allergen":      user_allergen or "",
        # DRI fields — blank if not calculated
        "dri_sex":            dri_inputs.get("sex", ""),
        "dri_age":            dri_inputs.get("age", ""),
        "dri_height_cm":      dri_inputs.get("height_cm", ""),
        "dri_weight_kg":      dri_inputs.get("weight_kg", ""),
        "dri_activity":       dri_inputs.get("activity", ""),
        "dri_bmi":            (dri or {}).get("bmi", ""),
        "dri_eer_kcal":       (dri or {}).get("eer", ""),
        # Scan fields
        "image_path":         image_path,
        "ingredients":        ingredients,
        "detected_allergens": allergens_str,
        "detected_additives": additives_str,
        "timestamp":          datetime.now().isoformat(timespec="seconds"),
    }

    with open(RECORDS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)

    return user_id

