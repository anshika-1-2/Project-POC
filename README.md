# 🍱 Japanese Food Label Allergen Scanner

Scan Japanese food packet images to detect allergens and additives.  
Users enter their name and any personal allergen. The app reads the label via OCR,
translates ingredients to English, runs detection, and saves the scan record.

---

## Project structure

```
food_allergen_app/
├── app.py                      # Streamlit frontend
├── requirements.txt
├── data/
│   ├── allergen_master.csv
│   ├── allergen_tokens.csv
│   ├── ingredient_sources.csv
│   ├── japanese_food_additives.csv
│   ├── scan_records.csv        # created on first scan
│   └── images/                 # saved label images
├── services/
│   ├── __init__.py
│   ├── detection.py            # detect_allergens, detect_additives, analyze_ingredients
│   ├── ocr.py                  # two-stage OCR + translation (Qwen2.5-VL-7B)
│   └── storage.py              # save scan records to CSV
└── utils/
    └── __init__.py
```

---

## Setup

### 1. Create and activate a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** PyTorch with CUDA is recommended for OCR speed.  
> The Qwen2.5-VL-7B model (~15 GB) is downloaded from HuggingFace on first run and cached automatically.

### 3. Run the app

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`.

---

## How it works

| Step | What happens |
|------|-------------|
| 1 | User enters name and optional personal allergen in the sidebar |
| 2 | User uploads a Japanese food label image |
| 3 | **Stage 1 OCR** — Qwen2.5-VL reads and copies exact Japanese characters |
| 4 | **Stage 2 Translate** — model translates Japanese JSON to English (no image re-read) |
| 5 | English ingredients list passed to `detect_allergens()` and `detect_additives()` |
| 6 | Results displayed: personal allergen alert + JP mandatory + recommended + additives |
| 7 | Record saved to `data/scan_records.csv` |

---

## Saved record fields

| Field | Description |
|-------|-------------|
| `user_id` | Auto-generated 8-char ID |
| `user_name` | Name entered by user |
| `user_allergen` | Allergen specified by user (if any) |
| `image_path` | Path to saved copy of the uploaded image |
| `ingredients` | Extracted English ingredient list |
| `detected_allergens` | Semicolon-separated allergen names |
| `detected_additives` | Semicolon-separated `id:name` pairs |
| `timestamp` | ISO 8601 timestamp |

---

## Allergen categories

| Category | Allergens |
|----------|-----------|
| JP Mandatory (法定表示) | egg, milk, wheat, buckwheat, peanut, shrimp, crab, walnut |
| JP Recommended (推奨表示) | abalone, almond, squid, salmon roe, orange, cashew nut, kiwi, beef, sesame, salmon, mackerel, soy, chicken, banana, pork, peach, yam, apple, gelatin, macadamia nut |
