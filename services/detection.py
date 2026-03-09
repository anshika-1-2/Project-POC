"""
detection.py
Allergen and additive detection functions.
Ported directly from Allergens_and_Additives.ipynb (cells 177-180).
"""

import re
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# ── Load data ─────────────────────────────────────────────────────────────────
allergen_master    = pd.read_csv(DATA_DIR / "allergen_master.csv")
allergen_tokens    = pd.read_csv(DATA_DIR / "allergen_tokens.csv")
ingredient_sources = pd.read_csv(DATA_DIR / "ingredient_sources.csv")
additives_df       = pd.read_csv(DATA_DIR / "japanese_food_additives.csv", on_bad_lines="skip")

# ── Build lookup tables ───────────────────────────────────────────────────────
id_to_allergen: dict[int, str] = dict(zip(
    allergen_master["allergen_id"].astype(int),
    allergen_master["canonical_name"].str.lower().str.strip(),
))

token_to_allergen_id: dict[str, int] = {}
for _, row in allergen_tokens.iterrows():
    if pd.notna(row["allergen_id"]) and pd.notna(row["token"]):
        token_to_allergen_id[str(row["token"]).lower().strip()] = int(row["allergen_id"])

for _, row in ingredient_sources.iterrows():
    if pd.notna(row["allergen_id"]) and pd.notna(row["ingredient"]):
        key = str(row["ingredient"]).lower().strip()
        if key not in token_to_allergen_id:
            token_to_allergen_id[key] = int(row["allergen_id"])

# Longest-first so multi-word phrases match before single words
sorted_tokens = sorted(token_to_allergen_id.keys(), key=len, reverse=True)

additives_df["name_normalized"] = additives_df["name"].str.lower().str.strip()

STOPWORDS = {
    "water", "salt", "sugar", "oil", "flour", "starch", "extract", "powder",
    "acid", "colour", "color", "red", "yellow", "blue", "black", "white",
    "green", "dry", "dried", "raw", "natural", "modified", "refined",
    "processed", "organic", "pure", "sauce",
}

# ── JP mandatory allergens (7 + walnut = 8) ───────────────────────────────────
JP_MANDATORY = {"egg", "milk", "wheat", "buckwheat", "peanut", "shrimp", "crab", "walnut"}


# ── Text helpers ──────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _expand_parens(text: str) -> list[str]:
    tokens = []
    for group in re.findall(r"\(([^)]+)\)", text):
        tokens.extend(t.strip() for t in group.split(","))
    outer = re.sub(r"\([^)]+\)", "", text)
    tokens.extend(t.strip() for t in outer.split(","))
    return [t for t in tokens if t]


def _tokenize(raw: str) -> list[str]:
    cleaned = _normalize(raw)
    tokens  = _expand_parens(cleaned)
    final: list[str] = []
    for t in tokens:
        final.extend(x.strip() for x in t.split(";"))
    return [t for t in final if len(t) > 1]


# ── Function 1: detect_allergens ─────────────────────────────────────────────
def detect_allergens(ingredient_text: str, return_df: bool = False):
    """
    Detect allergens from an ingredient list string.
    Returns sorted list of allergen names, or DataFrame if return_df=True.
    """
    tokens  = _tokenize(ingredient_text)
    results = []
    seen: set = set()

    for token in tokens:
        if token in token_to_allergen_id:
            aid = token_to_allergen_id[token]
            key = (token, aid)
            if key not in seen:
                seen.add(key)
                results.append({
                    "ingredient_token": token,
                    "matched_term":     token,
                    "allergen_id":      aid,
                    "allergen_name":    id_to_allergen.get(aid, "unknown"),
                    "match_type":       "exact",
                })
        else:
            for known in sorted_tokens:
                if known in token and known not in STOPWORDS and len(known) >= 3:
                    aid = token_to_allergen_id[known]
                    key = (token, aid)
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "ingredient_token": token,
                            "matched_term":     known,
                            "allergen_id":      aid,
                            "allergen_name":    id_to_allergen.get(aid, "unknown"),
                            "match_type":       "substring",
                        })
                    break

    empty_cols = ["ingredient_token", "matched_term", "allergen_id", "allergen_name", "match_type"]
    if not results:
        return pd.DataFrame(columns=empty_cols) if return_df else []

    df = (
        pd.DataFrame(results)
        .drop_duplicates(subset=["ingredient_token", "allergen_id"])
        .sort_values("allergen_name")
        .reset_index(drop=True)
    )
    return df if return_df else sorted(df["allergen_name"].unique().tolist())


# ── Function 2: detect_additives ─────────────────────────────────────────────
def _name_match(token: str, additive_name: str) -> bool:
    if token == additive_name:
        return True
    if token in STOPWORDS or len(token) < 6:
        return False
    if additive_name.startswith(token):
        return len(token) / len(additive_name) >= 0.6
    if token.startswith(additive_name):
        return len(additive_name) / len(token) >= 0.6
    return False


def detect_additives(ingredient_text: str, return_df: bool = False):
    """
    Detect Japanese food additives from an ingredient list string.
    Returns sorted list of additive names, or DataFrame if return_df=True.
    """
    tokens  = _tokenize(ingredient_text)
    results = []
    seen: set = set()

    for token in tokens:
        for _, row in additives_df.iterrows():
            additive_id   = str(int(row["additive_id"]))
            additive_name = str(row["name_normalized"]).strip()
            orig_name     = str(row["name"]).strip()
            note          = str(row["note"]).strip()

            id_matched   = bool(re.search(rf"(?<!\d){re.escape(additive_id)}(?!\d)", token))
            name_matched = _name_match(token, additive_name)

            if id_matched or name_matched:
                key = (token, additive_id)
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "ingredient_token": token,
                        "match_type":       "id" if id_matched else "name",
                        "additive_id":      int(row["additive_id"]),
                        "additive_name":    orig_name,
                        "note":             note,
                    })

    empty_cols = ["ingredient_token", "match_type", "additive_id", "additive_name", "note"]
    if not results:
        return pd.DataFrame(columns=empty_cols) if return_df else []

    df = (
        pd.DataFrame(results)
        .drop_duplicates(subset=["ingredient_token", "additive_id"])
        .sort_values("additive_id")
        .reset_index(drop=True)
    )
    return df if return_df else sorted(df["additive_name"].unique().tolist())


# ── Function 3: analyze_ingredients ─────────────────────────────────────────
def analyze_ingredients(ingredient_text: str) -> dict:
    """
    Run both detectors and return combined result dict.

    Returns
    -------
    dict with keys:
        allergens    → list of allergen name strings
        additives    → list of (additive_id, additive_name) tuples
        allergen_df  → detailed DataFrame
        additive_df  → detailed DataFrame
    """
    allergen_df = detect_allergens(ingredient_text, return_df=True)
    additive_df = detect_additives(ingredient_text, return_df=True)

    allergens = sorted(allergen_df["allergen_name"].unique().tolist()) if not allergen_df.empty else []
    additives = (
        list(zip(additive_df["additive_id"].tolist(), additive_df["additive_name"].tolist()))
        if not additive_df.empty else []
    )
    # Deduplicate additives by id
    seen_ids: set = set()
    unique_additives = []
    for aid, aname in additives:
        if aid not in seen_ids:
            seen_ids.add(aid)
            unique_additives.append((aid, aname))

    return {
        "allergens":   allergens,
        "additives":   unique_additives,
        "allergen_df": allergen_df,
        "additive_df": additive_df,
    }
