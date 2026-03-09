# """
# ocr.py
# Two-stage Japanese food label OCR and translation pipeline.
# Stage 1: image → exact Japanese JSON  (Qwen2.5-VL-7B-Instruct)
# Stage 2: Japanese JSON → English section  (text only, no image)
# Final JSON assembled in Python — model never re-copies the Japanese section.
# """

# import re
# import json
# import time
# import torch
# from pathlib import Path

# # Lazy-loaded globals (loaded once on first call)
# _model     = None
# _processor = None
# _device    = None

# # ── JP allergen translation map (post-processing fallback) ───────────────────
# JP_ALLERGEN_MAP = {
#     "小麦": "wheat",       "乳": "milk",           "卵": "egg",
#     "そば": "buckwheat",   "落花生": "peanut",      "えび": "shrimp",
#     "かに": "crab",        "くるみ": "walnut",      "大豆": "soy",
#     "ごま": "sesame",      "アーモンド": "almond",  "カシューナッツ": "cashew nut",
#     "牛肉": "beef",        "豚肉": "pork",          "鶏肉": "chicken",
#     "さけ": "salmon",      "さば": "mackerel",      "いか": "squid",
#     "あわび": "abalone",   "いくら": "salmon roe",  "オレンジ": "orange",
#     "バナナ": "banana",    "もも": "peach",         "りんご": "apple",
#     "ゼラチン": "gelatin", "まつたけ": "matsutake", "やまいも": "yam",
#     "マカダミアナッツ": "macadamia nut",
# }


# def _load_model():
#     global _model, _processor, _device
#     if _model is not None:
#         return

#     from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
#     from qwen_vl_utils import process_vision_info  # noqa: F401

#     model_id = "Qwen/Qwen2.5-VL-7B-Instruct"
#     _device  = "cuda" if torch.cuda.is_available() else "cpu"

#     _processor = AutoProcessor.from_pretrained(
#         model_id,
#         min_pixels=200 * 200,
#         max_pixels=1280 * 28 * 28,
#     )
#     _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
#         model_id,
#         torch_dtype=torch.bfloat16,
#         device_map="auto",
#     )
#     _model.eval()


# # ── Prompts ───────────────────────────────────────────────────────────────────
# SYSTEM_OCR = (
#     "You are a strict Japanese food label OCR engine.\n"
#     "Copy every character EXACTLY as printed. Do not correct, normalise, or infer anything.\n"
#     "Critical pairs — examine every occurrence carefully:\n"
#     "  ゲ(ge) vs グ(gu)  →  ゲル化剤 is correct, グル化剤 is WRONG\n"
#     "  ペ(pe) vs ベ(be)  →  ペクチン is correct, ベクチン is WRONG\n"
#     "  ソ vs ン  シ vs ツ  ー(long vowel) vs 一(kanji one)  ヴ vs ウ\n"
#     "Output ONLY valid JSON. No markdown, no prose."
# )

# PROMPT_OCR = (
#     "Read this Japanese food label and copy ONLY what is visibly printed.\n"
#     "Return ONLY this JSON — null for any section not present in the image:\n"
#     "{\n"
#     '  "product_name": null,\n'
#     '  "ingredients": { "raw_text": null, "items": [] },\n'
#     '  "allergens": { "style": null, "items": [] },\n'
#     '  "nutrition": { "basis": null, "calories": null, "protein": null,\n'
#     '               "fat": null, "carbohydrate": null, "salt": null }\n'
#     "}\n"
#     "OCR rules:\n"
#     "- Copy ingredient separators exactly: 、not ・ unless ・ is actually printed\n"
#     "- ingredients.raw_text: full 原材料名 line verbatim — every character, bracket, separator\n"
#     "- ingredients.items: split raw_text ONLY on 、keeping （allergen）attached to its ingredient\n"
#     "- allergens.items: list each term separately from （）brackets AND/OR after 一部に\n"
#     "- allergens.style: individual=（）per item | collective=一部に～を含む | both | none\n"
#     "- nutrition values: copy printed number+unit exactly e.g. 174kcal, 3.6g\n"
#     "- If a section is not visible in the image set all its fields to null"
# )

# SYSTEM_TRANSLATE = (
#     "You are a Japanese food label translation specialist.\n"
#     "Translate accurately using standard food industry English terms.\n"
#     "Output ONLY valid JSON. No markdown, no prose."
# )

# PROMPT_TRANSLATE_TEMPLATE = (
#     "Translate this Japanese food label JSON to English. Return ONLY valid JSON, no prose.\n"
#     "\n"
#     "INPUT:\n"
#     "{ocr_json}\n"
#     "\n"
#     'OUTPUT — {"english":{...}} with these fields translated:\n'
#     "- product_name: English name\n"
#     "- ingredients: translate each item; keep (allergen) annotation in English\n"
#     "- allergens.style: copy from input unchanged\n"
#     "- allergens.items: translate each allergen name\n"
#     "- nutrition.basis: translate serving note (e.g. 1袋(51g)当たり→per bag (51g), 100gあたり→per 100g)\n"
#     "- nutrition values: number only, no units (e.g. 174kcal→174, 3.6g→3.6)\n"
#     "\n"
#     "Allergens: 卵=egg 乳=milk 小麦=wheat そば=buckwheat 落花生=peanut えび=shrimp かに=crab "
#     "くるみ=walnut アーモンド=almond カシューナッツ=cashew nut キウイフルーツ=kiwi fruit "
#     "牛肉=beef 豚肉=pork 鶏肉=chicken さけ=salmon さば=mackerel いか=squid "
#     "あわび=abalone いくら=salmon roe オレンジ=orange バナナ=banana もも=peach "
#     "りんご=apple ゼラチン=gelatin 大豆=soy ごま=sesame まつたけ=matsutake "
#     "やまいも=yam マカダミアナッツ=macadamia nut\n"
#     "Food: 水あめ=starch syrup 砂糖=sugar でん粉=starch 植物油脂=vegetable oil "
#     "濃縮もも果汁=concentrated peach juice 濃縮果汁=concentrated juice "
#     "酸味料=acidulant ゲル化剤=gelling agent ペクチン=pectin "
#     "光沢剤=glazing agent 香料=flavoring 乳化剤=emulsifier "
#     "増粘剤=thickener 着色料=food coloring 保存料=preservative "
#     "甘味料=sweetener 酸化防止剤=antioxidant 調味料=seasoning "
#     "膨張剤=leavening agent 加工でん粉=modified starch "
#     "果糖ぶどう糖液糖=high-fructose corn syrup"
# )


# # ── Helpers ───────────────────────────────────────────────────────────────────
# def _safe_parse(raw: str):
#     candidates = [raw]
#     for m in re.findall(r"```(?:json)?\s*([\s\S]+?)```", raw):
#         candidates.insert(0, m.strip())
#     m2 = re.search(r"(\{[\s\S]+\})", raw)
#     if m2:
#         candidates.append(m2.group(1))
#     err = "no candidates"
#     for c in candidates:
#         try:
#             return json.loads(c.strip()), None
#         except json.JSONDecodeError as e:
#             err = str(e)
#     return None, err


# def _generate(messages: list, has_image: bool) -> tuple[str, float, int]:
#     from qwen_vl_utils import process_vision_info

#     # Process vision info BEFORE apply_chat_template so token count matches
#     image_inputs, video_inputs = process_vision_info(messages)

#     text_in = _processor.apply_chat_template(
#         messages, tokenize=False, add_generation_prompt=True
#     )

#     inputs = _processor(
#         text=[text_in],
#         images=image_inputs if has_image else None,
#         videos=video_inputs if has_image else None,
#         padding=True,
#         return_tensors="pt",
#     ).to(_device)

#     t0 = time.time()
#     with torch.no_grad():
#         out = _model.generate(
#             **inputs,
#             max_new_tokens=768,
#             temperature=0.05,
#             do_sample=True,
#             repetition_penalty=1.1,
#             top_p=0.9,
#         )
#     latency  = round(time.time() - t0, 2)
#     new_toks = out.shape[1] - inputs["input_ids"].shape[1]
#     raw = _processor.batch_decode(
#         out[:, inputs["input_ids"].shape[1]:],
#         skip_special_tokens=True,
#     )[0].strip()
#     return raw, latency, new_toks


# # ── Public API ────────────────────────────────────────────────────────────────
# def extract_label(image_path: str) -> dict:
#     """
#     Run the two-stage pipeline on a food label image.

#     Returns
#     -------
#     dict with keys:
#         japanese      → OCR dict (exact characters)
#         english       → translation dict
#         ingredients   → flat English ingredients list (for detection)
#         latency_s     → total wall-clock seconds
#         error         → str or None
#     """
#     _load_model()

#     # ── Stage 1: OCR ─────────────────────────────────────────────────────────
#     s1_messages = [
#         {"role": "system", "content": SYSTEM_OCR},
#         {"role": "user", "content": [
#             {"type": "image", "image": image_path},
#             {"type": "text",  "text": PROMPT_OCR},
#         ]},
#     ]
#     s1_raw, s1_lat, _ = _generate(s1_messages, has_image=True)
#     jp_parsed, jp_err = _safe_parse(s1_raw)

#     if jp_parsed is None:
#         return {
#             "japanese":    None,
#             "english":     None,
#             "ingredients": "",
#             "latency_s":   s1_lat,
#             "error":       f"OCR parse failed: {jp_err}",
#         }

#     # ── Stage 2: Translate (text only, no image) ──────────────────────────────
#     ocr_str = json.dumps(jp_parsed, ensure_ascii=False, indent=2)
#     prompt  = PROMPT_TRANSLATE_TEMPLATE.replace("{ocr_json}", ocr_str)
#     s2_messages = [
#         {"role": "system", "content": SYSTEM_TRANSLATE},
#         {"role": "user",   "content": [{"type": "text", "text": prompt}]},
#     ]
#     s2_raw, s2_lat, _ = _generate(s2_messages, has_image=False)
#     en_parsed, en_err = _safe_parse(s2_raw)

#     english = (en_parsed or {}).get("english") or en_parsed or {}
#     # After english = (en_parsed or {}).get("english") or en_parsed or {}
# # Add this fallback:

#     # If Stage 2 didn't populate nutrition, copy from JP OCR directly
#     # (numbers are universal — no translation needed)
#     en_nut = english.get("nutrition") or {}
#     jp_nut = jp_parsed.get("nutrition") or {}
#     if jp_nut and not any(en_nut.get(k) for k in ["calories_kcal", "protein_g", "fat_g"]):
#         # Map JP field names → EN field names used by parse_nutrition()
#         field_remap = {
#             "calories":     "calories_kcal",
#             "protein":      "protein_g",
#             "fat":          "fat_g",
#             "carbohydrate": "carbohydrate_g",
#             "salt":         "salt_g",
#             "basis":        "basis",
#         }
#         fallback_nut = {field_remap[k]: v for k, v in jp_nut.items() if k in field_remap and v is not None}
#         english["nutrition"] = fallback_nut
#     # ── Fix 1: translate allergen items left in Japanese by Stage 2 ──────────
#     en_alg = english.get("allergens") or {}
#     if isinstance(en_alg, dict):
#         en_alg["items"] = [
#             JP_ALLERGEN_MAP.get(str(item).strip(), str(item))
#             for item in (en_alg.get("items") or [])
#         ]
#         english["allergens"] = en_alg

#     # Also translate allergen items directly from JP OCR as fallback
#     jp_alg_items = (jp_parsed.get("allergens") or {}).get("items") or []
#     translated_jp = [
#         JP_ALLERGEN_MAP.get(str(i).strip(), str(i)) for i in jp_alg_items
#     ]

#     # ── Fix 2 + 3: flatten ingredients, strip leading "/" per token ───────────
#     raw_items = english.get("ingredients") or []
#     if isinstance(raw_items, list):
#         split_items = []
#         for item in raw_items:
#             clean = str(item).lstrip("/").strip()
#             split_items.extend(p.strip() for p in clean.split("/") if p.strip())
#         ingredients_flat = ", ".join(split_items)
#     elif isinstance(raw_items, dict):
#         items = raw_items.get("items") or []
#         split_items = []
#         for item in items:
#             clean = str(item).lstrip("/").strip()
#             split_items.extend(p.strip() for p in clean.split("/") if p.strip())
#         ingredients_flat = ", ".join(split_items)
#     else:
#         ingredients_flat = ", ".join(
#             p.strip() for p in str(raw_items).lstrip("/").split("/") if p.strip()
#         )

#     # Append translated allergen terms so detector always sees them
#     all_allergen_terms = list(dict.fromkeys(
#         (en_alg.get("items") or []) + translated_jp
#     ))
#     if all_allergen_terms:
#         alg_text = ", ".join(str(i) for i in all_allergen_terms)
#         ingredients_flat = (ingredients_flat + ", " + alg_text).strip(", ")

#     # ── Fix 4: strip secondary bracketed values from nutrition fields ─────────
#     # e.g. "76kcal (70kcal)" → "76kcal",  "6.2g ( 6.0g)" → "6.2g"
#     nut_en = english.get("nutrition") or {}
#     for k, v in nut_en.items():
#         if isinstance(v, str):
#             nut_en[k] = re.sub(r"\s*\(.*?\)", "", v).strip()
#     english["nutrition"] = nut_en

#     return {
#         "japanese":    jp_parsed,
#         "english":     english,
#         "ingredients": ingredients_flat,
#         "latency_s":   round(s1_lat + s2_lat, 2),
#         "error":       en_err if en_parsed is None else None,
#     }

"""
ocr.py
Two-stage Japanese food label OCR and translation pipeline.
Stage 1: image → exact Japanese JSON  (Qwen2.5-VL-7B-Instruct)
Stage 2: Japanese JSON → English section  (text only, no image)
Final JSON assembled in Python — model never re-copies the Japanese section.
"""

import re
import json
import time
import torch
from pathlib import Path

# Lazy-loaded globals (loaded once on first call)
_model     = None
_processor = None
_device    = None

# ── JP allergen translation map (post-processing fallback) ───────────────────
JP_ALLERGEN_MAP = {
    "小麦": "wheat",       "乳": "milk",           "卵": "egg",
    "そば": "buckwheat",   "落花生": "peanut",      "えび": "shrimp",
    "かに": "crab",        "くるみ": "walnut",      "大豆": "soy",
    "ごま": "sesame",      "アーモンド": "almond",  "カシューナッツ": "cashew nut",
    "牛肉": "beef",        "豚肉": "pork",          "鶏肉": "chicken",
    "さけ": "salmon",      "さば": "mackerel",      "いか": "squid",
    "あわび": "abalone",   "いくら": "salmon roe",  "オレンジ": "orange",
    "バナナ": "banana",    "もも": "peach",         "りんご": "apple",
    "ゼラチン": "gelatin", "まつたけ": "matsutake", "やまいも": "yam",
    "マカダミアナッツ": "macadamia nut",
}


def _load_model():
    global _model, _processor, _device
    if _model is not None:
        return

    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
    from qwen_vl_utils import process_vision_info  # noqa: F401

    model_id = "Qwen/Qwen2.5-VL-7B-Instruct"
    _device  = "cuda" if torch.cuda.is_available() else "cpu"

    _processor = AutoProcessor.from_pretrained(
        model_id,
        min_pixels=200 * 200,
        max_pixels=1280 * 28 * 28,
    )
    _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )
    _model.eval()


# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_OCR = (
    "You are a strict Japanese food label OCR engine.\n"
    "Copy every character EXACTLY as printed. Do not correct, normalise, or infer anything.\n"
    "Critical pairs — examine every occurrence carefully:\n"
    "  ゲ(ge) vs グ(gu)  →  ゲル化剤 is correct, グル化剤 is WRONG\n"
    "  ペ(pe) vs ベ(be)  →  ペクチン is correct, ベクチン is WRONG\n"
    "  ソ vs ン  シ vs ツ  ー(long vowel) vs 一(kanji one)  ヴ vs ウ\n"
    "Output ONLY valid JSON. No markdown, no prose."
)

PROMPT_OCR = (
    "Read this Japanese food label and copy ONLY what is visibly printed.\n"
    "Return ONLY this JSON — null for any section not present in the image:\n"
    "{\n"
    '  "product_name": null,\n'
    '  "ingredients": { "raw_text": null, "items": [] },\n'
    '  "allergens": { "style": null, "items": [] },\n'
    '  "nutrition": { "basis": null, "calories": null, "protein": null,\n'
    '               "fat": null, "carbohydrate": null, "salt": null }\n'
    "}\n"
    "OCR rules:\n"
    "- Copy ingredient separators exactly: 、not ・ unless ・ is actually printed\n"
    "- ingredients.raw_text: full 原材料名 line verbatim — every character, bracket, separator\n"
    "- ingredients.items: split raw_text ONLY on 、keeping （allergen）attached to its ingredient\n"
    "- allergens.items: list each term separately from （）brackets AND/OR after 一部に\n"
    "- allergens.style: individual=（）per item | collective=一部に～を含む | both | none\n"
    "- nutrition values: copy printed number+unit exactly e.g. 174kcal, 3.6g\n"
    "- If a section is not visible in the image set all its fields to null"
)

SYSTEM_TRANSLATE = (
    "You are a Japanese food label translation specialist.\n"
    "Translate accurately using standard food industry English terms.\n"
    "Output ONLY valid JSON. No markdown, no prose."
)

PROMPT_TRANSLATE_TEMPLATE = (
    "Translate this Japanese food label JSON to English. Return ONLY valid JSON, no prose.\n"
    "\n"
    "INPUT:\n"
    "{ocr_json}\n"
    "\n"
    'OUTPUT — {"english":{...}} with these fields translated:\n'
    "- product_name: English name\n"
    "- ingredients: translate each item; keep (allergen) annotation in English\n"
    "- allergens.style: copy from input unchanged\n"
    "- allergens.items: translate each allergen name\n"
    "- nutrition.basis: translate serving note (e.g. 1袋(51g)当たり→per bag (51g), 100gあたり→per 100g)\n"
    "- nutrition values: number only, no units (e.g. 174kcal→174, 3.6g→3.6)\n"
    "\n"
    "Allergens: 卵=egg 乳=milk 小麦=wheat そば=buckwheat 落花生=peanut えび=shrimp かに=crab "
    "くるみ=walnut アーモンド=almond カシューナッツ=cashew nut キウイフルーツ=kiwi fruit "
    "牛肉=beef 豚肉=pork 鶏肉=chicken さけ=salmon さば=mackerel いか=squid "
    "あわび=abalone いくら=salmon roe オレンジ=orange バナナ=banana もも=peach "
    "りんご=apple ゼラチン=gelatin 大豆=soy ごま=sesame まつたけ=matsutake "
    "やまいも=yam マカダミアナッツ=macadamia nut\n"
    "Food: 水あめ=starch syrup 砂糖=sugar でん粉=starch 植物油脂=vegetable oil "
    "濃縮もも果汁=concentrated peach juice 濃縮果汁=concentrated juice "
    "酸味料=acidulant ゲル化剤=gelling agent ペクチン=pectin "
    "光沢剤=glazing agent 香料=flavoring 乳化剤=emulsifier "
    "増粘剤=thickener 着色料=food coloring 保存料=preservative "
    "甘味料=sweetener 酸化防止剤=antioxidant 調味料=seasoning "
    "膨張剤=leavening agent 加工でん粉=modified starch "
    "果糖ぶどう糖液糖=high-fructose corn syrup"
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_parse(raw: str):
    candidates = [raw]
    for m in re.findall(r"```(?:json)?\s*([\s\S]+?)```", raw):
        candidates.insert(0, m.strip())
    m2 = re.search(r"(\{[\s\S]+\})", raw)
    if m2:
        candidates.append(m2.group(1))
    err = "no candidates"
    for c in candidates:
        try:
            return json.loads(c.strip()), None
        except json.JSONDecodeError as e:
            err = str(e)
    return None, err


def _generate(messages: list, has_image: bool) -> tuple[str, float, int]:
    from qwen_vl_utils import process_vision_info

    # Process vision info BEFORE apply_chat_template so token count matches
    image_inputs, video_inputs = process_vision_info(messages)

    text_in = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    inputs = _processor(
        text=[text_in],
        images=image_inputs if has_image else None,
        videos=video_inputs if has_image else None,
        padding=True,
        return_tensors="pt",
    ).to(_device)

    t0 = time.time()
    with torch.no_grad():
        out = _model.generate(
            **inputs,
            max_new_tokens=768,
            temperature=0.05,
            do_sample=True,
            repetition_penalty=1.1,
            top_p=0.9,
        )
    latency  = round(time.time() - t0, 2)
    new_toks = out.shape[1] - inputs["input_ids"].shape[1]
    raw = _processor.batch_decode(
        out[:, inputs["input_ids"].shape[1]:],
        skip_special_tokens=True,
    )[0].strip()
    return raw, latency, new_toks


# ── Public API ────────────────────────────────────────────────────────────────
def extract_label(image_path: str) -> dict:
    """
    Run the two-stage pipeline on a food label image.

    Returns
    -------
    dict with keys:
        japanese      → OCR dict (exact characters)
        english       → translation dict
        ingredients   → flat English ingredients list (for detection)
        latency_s     → total wall-clock seconds
        error         → str or None
    """
    _load_model()

    # ── Stage 1: OCR ─────────────────────────────────────────────────────────
    s1_messages = [
        {"role": "system", "content": SYSTEM_OCR},
        {"role": "user", "content": [
            {"type": "image", "image": image_path},
            {"type": "text",  "text": PROMPT_OCR},
        ]},
    ]
    s1_raw, s1_lat, _ = _generate(s1_messages, has_image=True)
    jp_parsed, jp_err = _safe_parse(s1_raw)

    if jp_parsed is None:
        return {
            "japanese":    None,
            "english":     None,
            "ingredients": "",
            "latency_s":   s1_lat,
            "error":       f"OCR parse failed: {jp_err}",
        }

    # ── Stage 2: Translate (text only, no image) ──────────────────────────────
    ocr_str = json.dumps(jp_parsed, ensure_ascii=False, indent=2)
    prompt  = PROMPT_TRANSLATE_TEMPLATE.replace("{ocr_json}", ocr_str)
    s2_messages = [
        {"role": "system", "content": SYSTEM_TRANSLATE},
        {"role": "user",   "content": [{"type": "text", "text": prompt}]},
    ]
    s2_raw, s2_lat, _ = _generate(s2_messages, has_image=False)
    en_parsed, en_err = _safe_parse(s2_raw)

    english = (en_parsed or {}).get("english") or en_parsed or {}

    # ── Fix 1: translate allergen items left in Japanese by Stage 2 ──────────
    en_alg = english.get("allergens") or {}
    if isinstance(en_alg, dict):
        en_alg["items"] = [
            JP_ALLERGEN_MAP.get(str(item).strip(), str(item))
            for item in (en_alg.get("items") or [])
        ]
        english["allergens"] = en_alg

    # Also translate allergen items directly from JP OCR as fallback
    jp_alg_items = (jp_parsed.get("allergens") or {}).get("items") or []
    translated_jp = [
        JP_ALLERGEN_MAP.get(str(i).strip(), str(i)) for i in jp_alg_items
    ]

    # ── Fix 2 + 3: flatten ingredients, strip leading "/" per token ───────────
    raw_items = english.get("ingredients") or []
    if isinstance(raw_items, list):
        split_items = []
        for item in raw_items:
            clean = str(item).lstrip("/").strip()
            split_items.extend(p.strip() for p in clean.split("/") if p.strip())
        ingredients_flat = ", ".join(split_items)
    elif isinstance(raw_items, dict):
        items = raw_items.get("items") or []
        split_items = []
        for item in items:
            clean = str(item).lstrip("/").strip()
            split_items.extend(p.strip() for p in clean.split("/") if p.strip())
        ingredients_flat = ", ".join(split_items)
    else:
        ingredients_flat = ", ".join(
            p.strip() for p in str(raw_items).lstrip("/").split("/") if p.strip()
        )

    # Append translated allergen terms so detector always sees them
    all_allergen_terms = list(dict.fromkeys(
        (en_alg.get("items") or []) + translated_jp
    ))
    if all_allergen_terms:
        alg_text = ", ".join(str(i) for i in all_allergen_terms)
        ingredients_flat = (ingredients_flat + ", " + alg_text).strip(", ")

    # ── Fix 4: strip secondary bracketed values from nutrition fields ─────────
    # e.g. "76kcal (70kcal)" → "76kcal",  "6.2g ( 6.0g)" → "6.2g"
    nut_en = english.get("nutrition") or {}
    for k, v in nut_en.items():
        if isinstance(v, str):
            nut_en[k] = re.sub(r"\s*\(.*?\)", "", v).strip()
    english["nutrition"] = nut_en

    # ── Fix 5: translate Japanese "basis" field if Stage 2 left it in Japanese ──
    basis = nut_en.get("basis") or ""
    if basis and any(ord(c) > 0x3000 for c in basis):
        # Common patterns: コップ1杯（200ml）当り, 1袋(51g)当たり, 100gあたり
        basis_en = re.sub(r"当[たり]+|当り", "", basis)          # strip 当たり / 当り
        basis_en = re.sub(r"あたり", "", basis_en)               # strip あたり
        basis_en = re.sub(r"コップ\s*(\d+)\s*杯", r"\1 cup", basis_en)
        basis_en = re.sub(r"(\d+)\s*袋", r"\1 bag", basis_en)
        basis_en = re.sub(r"(\d+)\s*本", r"\1 piece", basis_en)
        basis_en = re.sub(r"(\d+)\s*枚", r"\1 slice", basis_en)
        basis_en = re.sub(r"(\d+)\s*個", r"\1 piece", basis_en)
        basis_en = re.sub(r"(\d+)\s*食", r"\1 serving", basis_en)
        basis_en = re.sub(r"100\s*g", "per 100g", basis_en)
        # Strip leftover Japanese characters, clean up spaces
        basis_en = re.sub(r"[　-鿿＀-￯]+", " ", basis_en).strip()
        basis_en = re.sub(r"\s+", " ", basis_en).strip(" ·-")
        if basis_en:
            nut_en["basis"] = basis_en
            english["nutrition"] = nut_en

    return {
        "japanese":    jp_parsed,
        "english":     english,
        "ingredients": ingredients_flat,
        "latency_s":   round(s1_lat + s2_lat, 2),
        "error":       en_err if en_parsed is None else None,
    }