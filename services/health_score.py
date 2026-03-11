"""
health_score.py
Personalised health scoring for Japanese food products.

Scoring rubric (total 100 pts):
  A) Ingredient quality     30 pts  (order + red-flag ingredient check)
  B) Macronutrient balance  30 pts  (sugar, sat-fat, protein, fibre)
  C) Sodium                 20 pts
  D) Additive risk          20 pts  (tiered: benign / caution / avoid)

Final verdict:
  70–100 → Healthy
  40–69  → Moderate
  0–39   → Unhealthy

Personalisation: if user has a DRI profile, thresholds for B and C are
scaled relative to the user's EER and sodium AI (not fixed per-100g values).
"""

from __future__ import annotations
import re

# ── Additive risk tiers ───────────────────────────────────────────────────────
# tier 0 = benign, tier 1 = caution, tier 2 = avoid
ADDITIVE_TIERS: dict[str, int] = {
    # Benign / functional
    "vitamin c": 0, "ascorbic acid": 0, "tocopherol": 0, "vitamin e": 0,
    "vitamin b1": 0, "vitamin b2": 0, "beta-carotene": 0,
    "lecithin": 0, "pectin": 0, "agar": 0, "xanthan gum": 0,
    "guar gum": 0, "locust bean gum": 0, "gellan gum": 0,
    "citric acid": 0, "lactic acid": 0, "malic acid": 0,
    "calcium carbonate": 0, "sodium bicarbonate": 0,
    "beeswax": 0, "carnauba wax": 0,
    # Caution
    "sodium nitrite": 1, "sodium nitrate": 1,
    "potassium sorbate": 1, "sodium benzoate": 1,
    "bha": 1, "bht": 1, "tbhq": 1,
    "carrageenan": 1, "modified starch": 1,
    "high-fructose corn syrup": 1, "glucose syrup": 1, "starch syrup": 1,
    "acesulfame": 1, "sucralose": 1, "aspartame": 1,
    "sodium glutamate": 1, "msg": 1, "monosodium glutamate": 1,
    "caramel color": 1, "caramel colour": 1,
    "artificial flavor": 1, "artificial flavoring": 1,
    # Avoid
    "potassium bromate": 2, "brominated vegetable oil": 2,
    "red 40": 2, "yellow 5": 2, "yellow 6": 2, "blue 1": 2, "blue 2": 2,
    "red 3": 2, "titanium dioxide": 2,
    "propyl gallate": 2, "propylene glycol": 2,
    "azodicarbonamide": 2,
    "caramel color iv": 2, "caramel colour iv": 2,
}

# ── Ingredient red-flags (heavy penalty if near top of list) ──────────────────
RED_FLAG_INGREDIENTS = [
    "sugar", "high-fructose corn syrup", "glucose syrup", "starch syrup",
    "white sugar", "brown sugar", "corn syrup", "fructose", "dextrose",
    "refined flour", "wheat flour", "bleached flour",
    "lard", "shortening", "partially hydrogenated",
    "palm oil", "palm shortening",
    "artificial flavor", "artificial colour", "artificial color",
]

POSITIVE_INGREDIENTS = [
    "whole grain", "whole wheat", "oat", "brown rice", "quinoa",
    "olive oil", "flaxseed", "chia", "walnut", "almond",
    "spinach", "kale", "broccoli", "carrot", "tomato",
    "legume", "lentil", "chickpea", "kidney bean", "edamame",
    "tofu", "natto", "miso", "tempeh",
    "green tea", "matcha",
]


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[()（）\[\]{}]", " ", text)
    tokens = [t.strip().strip(".,;:") for t in re.split(r"[,、/]", text)]
    return [t for t in tokens if len(t) > 1]


def _get_num(nutrition: dict, *keys) -> float | None:
    for k in keys:
        v = nutrition.get(k)
        if v is not None:
            try:
                return float(re.search(r"\d+\.?\d*", str(v)).group())
            except (AttributeError, ValueError):
                pass
    return None


# ── Section A: Ingredient quality (0–30) ─────────────────────────────────────

def _score_ingredients(ingredients_flat: str) -> tuple[int, list[str], list[str]]:
    tokens = _tokenize(ingredients_flat)
    if not tokens:
        return 15, ["No ingredients data"], []   # neutral if missing

    score   = 30
    flags   = []
    boosts  = []

    # Red flags in top-3 positions are worse
    for i, tok in enumerate(tokens[:10]):
        for rf in RED_FLAG_INGREDIENTS:
            if rf in tok:
                penalty = 8 if i < 3 else 4
                score  -= penalty
                flags.append(f"{'⚠️ High' if i < 3 else 'Low'} position: {rf.title()}")
                break

    # Positive ingredients
    for tok in tokens:
        for pos in POSITIVE_INGREDIENTS:
            if pos in tok:
                score += 2
                boosts.append(f"Contains {pos.title()}")
                break

    # Ingredient count — very short lists are suspicious (ultra-processed)
    if len(tokens) == 1:
        boosts.append("Minimal ingredients")
    elif len(tokens) > 20:
        flags.append("Many ingredients (highly processed)")
        score -= 3

    return max(0, min(30, score)), flags[:4], boosts[:3]


# ── Section B: Macronutrient balance (0–30) ───────────────────────────────────

def _score_macros(
    nutrition: dict,
    dri_raw:   dict | None,
    serving_kcal: float | None,
) -> tuple[int, list[str], list[str]]:
    """
    Score is data-coverage weighted: each macro signal contributes up to its
    max points only if data is present. Missing data → that signal is skipped
    and max is reduced proportionally so score stays honest.

    Signals and their max contribution:
      sugar        : 12 pts (penalty focus)
      saturated fat: 10 pts (penalty focus)
      fibre        :  5 pts (bonus)
      protein      :  3 pts (bonus)
    Total possible: 30 pts
    """
    flags  = []
    boosts = []
    earned = 0   # points actually earned
    possible = 0  # max points for signals we have data for
    kcal = serving_kcal

    # Sugar (up to 12 pts)
    sugar = _get_num(nutrition, "sugar", "sugar_g", "sugars")
    # Sanity: sugar cannot equal carbohydrate — if identical, Stage 2 mis-mapped carbs→sugar
    carb_check = _get_num(nutrition, "carbohydrate", "carbohydrate_g", "carbs")
    if sugar is not None and carb_check is not None and abs(sugar - carb_check) < 0.1:
        sugar = None  # discard: identical to carbs → hallucinated
    if sugar is not None:
        possible += 12
        threshold = max(5.0, (kcal * 0.10 / 4)) if kcal else 10.0
        if sugar > threshold * 2:
            earned += 0;  flags.append(f"Very high sugar: {sugar}g")
        elif sugar > threshold:
            earned += 6;  flags.append(f"High sugar: {sugar}g")
        else:
            earned += 12; boosts.append(f"Low sugar: {sugar}g")

    # Saturated fat (up to 10 pts)
    sat_fat = _get_num(nutrition, "saturated_fat", "saturated_fat_g")
    if sat_fat is not None:
        possible += 10
        threshold = max(2.0, (kcal * 0.10 / 9)) if kcal else 4.0
        if sat_fat > threshold * 2:
            earned += 0;  flags.append(f"Very high saturated fat: {sat_fat}g")
        elif sat_fat > threshold:
            earned += 5;  flags.append(f"High saturated fat: {sat_fat}g")
        else:
            earned += 10; boosts.append(f"Low saturated fat: {sat_fat}g")

    # Fibre — bonus (up to 5 pts)
    fibre = _get_num(nutrition, "fibre", "fibre_g", "fiber", "dietary_fibre")
    if fibre is not None:
        possible += 5
        if fibre >= 3.0:
            earned += 5; boosts.append(f"Good fibre: {fibre}g")
        elif fibre >= 1.5:
            earned += 3; boosts.append(f"Some fibre: {fibre}g")
        else:
            earned += 1

    # Protein — bonus (up to 3 pts)
    protein = _get_num(nutrition, "protein", "protein_g")
    if protein is not None:
        possible += 3
        threshold = max(5.0, (kcal * 0.15 / 4)) if kcal else 5.0
        if protein >= threshold:
            earned += 3; boosts.append(f"Good protein: {protein}g")
        else:
            earned += 1

    # No nutrition data at all → neutral 15/30 with a note
    if possible == 0:
        return 15, ["No nutrition data — macros unscored"], []

    # Scale earned to 30 pts proportionally based on data coverage
    score = round(earned / possible * 30)
    return max(0, min(30, score)), flags[:4], boosts[:4]


# ── Section C: Sodium (0–20) ──────────────────────────────────────────────────

def _score_sodium(
    nutrition: dict,
    dri_raw:   dict | None,
) -> tuple[int, list[str], list[str]]:
    score  = 20
    flags  = []
    boosts = []

    salt = _get_num(nutrition, "salt", "salt_g", "sodium_g", "sodium")
    if salt is None:
        return 10, ["No sodium data — sodium unscored"], []  # 10/20 neutral midpoint

    # Convert salt → sodium (sodium = salt / 2.54) for DRI comparison
    sodium_g = round(salt / 2.54, 3)

    # DRI daily sodium AI (default 1.5g)
    daily_sodium = (dri_raw or {}).get("sodium_g") or 1.5
    pct_daily    = sodium_g / daily_sodium * 100

    if pct_daily > 40:
        score -= 16; flags.append(f"Very high sodium: {pct_daily:.0f}% of daily AI")
    elif pct_daily > 20:
        score -= 8;  flags.append(f"Moderate sodium: {pct_daily:.0f}% of daily AI")
    elif pct_daily > 10:
        score -= 3;  flags.append(f"Some sodium: {pct_daily:.0f}% of daily AI")
    else:
        boosts.append(f"Low sodium: {pct_daily:.0f}% of daily AI")

    return max(0, min(20, score)), flags, boosts


# ── Section D: Additive risk (0–20) ──────────────────────────────────────────

def _score_additives(
    additives: list[tuple],   # [(id, name), ...]
) -> tuple[int, list[str], list[str]]:
    if not additives:
        return 20, [], ["No additives detected"]

    score  = 20
    flags  = []
    boosts = []
    tier2_found = []
    tier1_found = []

    for _id, name in additives:
        name_l = name.lower()
        tier   = ADDITIVE_TIERS.get(name_l, 1)  # unknown → caution
        if tier == 2:
            tier2_found.append(name)
        elif tier == 1:
            tier1_found.append(name)

    score -= len(tier2_found) * 8
    score -= len(tier1_found) * 3

    for n in tier2_found[:2]:
        flags.append(f"Avoid additive: {n}")
    for n in tier1_found[:3]:
        flags.append(f"Caution additive: {n}")

    if not tier2_found and not tier1_found:
        boosts.append("Only benign additives")

    return max(0, min(20, score)), flags[:5], boosts


# ── Public API ────────────────────────────────────────────────────────────────

def compute_health_score(
    ingredients_flat: str,
    nutrition:        dict | None,
    additives:        list[tuple] | None,
    dri_raw:          dict | None = None,
) -> dict:
    """
    Compute a 0–100 personalised health score.

    Returns
    -------
    {
      "score":    int (0–100),
      "verdict":  "Healthy" | "Moderate" | "Unhealthy",
      "grade":    "A" | "B" | "C" | "D" | "F",
      "sections": {
        "ingredients": {"score":int, "max":30, "flags":[], "boosts":[]},
        "macros":      {"score":int, "max":30, ...},
        "sodium":      {"score":int, "max":20, ...},
        "additives":   {"score":int, "max":20, ...},
      },
      "top_flags":  [str, ...],   # up to 4 most important negatives
      "top_boosts": [str, ...],   # up to 3 most important positives
    }
    """
    nutrition = nutrition or {}
    additives = additives or []

    serving_kcal = _get_num(nutrition, "calories", "calories_kcal")

    sa, fa, ba = _score_ingredients(ingredients_flat)
    sb, fb, bb = _score_macros(nutrition, dri_raw, serving_kcal)
    sc, fc, bc = _score_sodium(nutrition, dri_raw)
    sd, fd, bd = _score_additives(additives)

    total = sa + sb + sc + sd

    if total >= 70:
        verdict = "Healthy"
    elif total >= 40:
        verdict = "Moderate"
    else:
        verdict = "Unhealthy"

    if total >= 85: grade = "A"
    elif total >= 70: grade = "B"
    elif total >= 55: grade = "C"
    elif total >= 40: grade = "D"
    else: grade = "F"

    all_flags  = fa + fb + fc + fd
    all_boosts = ba + bb + bc + bd

    return {
        "score":   total,
        "verdict": verdict,
        "grade":   grade,
        "sections": {
            "ingredients": {"score": sa, "max": 30, "flags": fa, "boosts": ba},
            "macros":      {"score": sb, "max": 30, "flags": fb, "boosts": bb},
            "sodium":      {"score": sc, "max": 20, "flags": fc, "boosts": bc},
            "additives":   {"score": sd, "max": 20, "flags": fd, "boosts": bd},
        },
        "top_flags":  list(dict.fromkeys(all_flags))[:4],
        "top_boosts": list(dict.fromkeys(all_boosts))[:3],
    }