"""
ocr.py — Two-stage Japanese food label OCR pipeline.
Stage 1: image → Japanese JSON  (Qwen2.5-VL)
Stage 2: Japanese JSON → English JSON  (text only)
"""

import re
import json
import time
import torch

_model = _processor = _device = None

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
    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor, BitsAndBytesConfig
    from qwen_vl_utils import process_vision_info  # noqa
    _device = "cuda" if torch.cuda.is_available() else "cpu"
    model_id = "Qwen/Qwen2.5-VL-7B-Instruct"
    _processor = AutoProcessor.from_pretrained(
        model_id, min_pixels=200*200, max_pixels=1280*28*28
    )
    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
    )
    _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_id,
        quantization_config=quant_config,
        device_map="auto",
    )
    _model.eval()


SYSTEM_OCR = (
    "You are a strict Japanese food label OCR engine.\n"
    "Copy every character EXACTLY as printed. Do not correct, normalise, or infer.\n"
    "Critical katakana pairs: ゲ(ge)≠グ(gu)  ペ(pe)≠ベ(be)  ソ≠ン  シ≠ツ  ー≠一  ヴ≠ウ\n"
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
    '               "fat": null, "saturated_fat": null,\n'
    '               "carbohydrate": null, "sugar": null,\n'
    '               "fibre": null, "salt": null }\n'
    "}\n"
    "Rules:\n"
    "- ingredients.raw_text: copy ONLY the content after the 原材料名 label — verbatim\n"
    "- ingredients.items: split raw_text on 、(Japanese comma) only\n"
    "- allergens.items: extract ONLY from the 本品に含まれているアレルゲン section OR （）brackets OR after 一部に OR after （N品目中）\n"
    "- allergens.items: DO NOT include allergens from 共通の設備 (cross-contact manufacturing) statements\n"
    "- allergens.style: individual | collective | both | none\n"
    "- nutrition: copy each number+unit exactly as printed (e.g. 174kcal, 3.6g, 0g)\n"
    "- sugar field: ONLY 糖質 or うち糖類 row — null if not present; do NOT use 食塩相当量\n"
    "- salt field: 食塩相当量 row — copy number+unit exactly\n"
    "- saturated_fat: 飽和脂肪酸 row — null if not present\n"
    "- fibre: 食物繊維 row — null if not present\n"
    "- If two nutrition columns exist (per bag + per 100g), use the per-bag column\n"
    "- Set all fields null if their section is not visible"
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
    "- ingredients.items: translate each item; keep (allergen) annotation in English\n"
    "- allergens.style: copy from input unchanged\n"
    "- allergens.items: translate each allergen name to English\n"
    "- nutrition.basis: translate serving note (e.g. 1袋(51g)当たり→per bag (51g), 100gあたり→per 100g)\n"
    "- nutrition values: number only, no units (e.g. 174kcal→174, 3.6g→3.6, 0g→0)\n"
    "- nutrition.sugar: ONLY 糖質 row value, number only — null if no separate 糖質 row exists\n"
    "- nutrition.salt: 食塩相当量 value, number only — do NOT confuse with sugar\n"
    "- nutrition.saturated_fat: 飽和脂肪酸 value, number only — null if absent\n"
    "- nutrition.fibre: 食物繊維 value, number only — null if absent\n"
    "- 乳脂肪分 and 無脂乳固形分 are composition % specs — set fat to null if only % present\n"
    "- 0g and 0% both become 0\n"
    "- ingredients: do NOT include product slogans or handling text — only ingredient names\n"
    "- allergens.items: only allergens that are IN the product — exclude cross-contact (共通設備) allergens\n"
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
    "果糖ぶどう糖液糖=high-fructose corn syrup "
    "ベニバナ色素=safflower pigment 紅花色素=safflower pigment "
    "カロテン=carotene アントシアニン=anthocyanin "
    "ビタミンC=vitamin C もち米=glutinous rice 味噌=miso 醤油=soy sauce みりん=mirin 酢=vinegar 食塩=salt 食用油=edible oil チキン=chicken でん粉糖=starch sugar 乳糖=lactose "
)


def _safe_parse(raw: str):
    if not raw or not raw.strip():
        return None, "empty model output"
    candidates = []
    for m in re.findall(r"```(?:json)?\s*([\s\S]+?)```", raw):
        candidates.append(m.strip())
    candidates.append(raw.strip())
    m2 = re.search(r"(\{[\s\S]+\})", raw)
    if m2:
        candidates.append(m2.group(1))
    for c in list(candidates):
        if c.count("{") > c.count("}"):
            candidates.append(c + "}" * (c.count("{") - c.count("}")))
    err = "no valid JSON found"
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        try:
            return json.loads(c), None
        except json.JSONDecodeError as e:
            err = str(e)
    return None, err


def _generate(messages, has_image, max_new_tokens=768):
    from qwen_vl_utils import process_vision_info
    image_inputs, video_inputs = process_vision_info(messages)
    text_in = _processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = _processor(
        text=[text_in],
        images=image_inputs if has_image else None,
        videos=video_inputs if has_image else None,
        padding=True, return_tensors="pt",
    ).to(_device)
    t0 = time.time()
    with torch.no_grad():
        out = _model.generate(
            **inputs, max_new_tokens=max_new_tokens,
            do_sample=False,
            repetition_penalty=1.1,
        )
    latency = round(time.time() - t0, 2)
    new_toks = out.shape[1] - inputs["input_ids"].shape[1]
    raw = _processor.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()
    return raw, latency, new_toks


def _smart_split(text):
    """Split on 、only outside （）brackets. Do NOT split on / — in JP labels / means
    alternative names (e.g. 味噌/調味料 = miso a.k.a. seasoning), not a separator."""
    parts, current, depth = [], [], 0
    for ch in text:
        if ch in "（(": depth += 1
        elif ch in "）)": depth = max(0, depth - 1)
        if ch == "、" and depth == 0:
            tok = "".join(current).strip()
            if tok: parts.append(tok)
            current = []
        else:
            current.append(ch)
    tok = "".join(current).strip()
    if tok: parts.append(tok)
    return parts


def extract_label(image_path: str) -> dict:
    _load_model()

    # Stage 1: OCR
    s1_raw, s1_lat, _ = _generate([
        {"role": "system", "content": SYSTEM_OCR},
        {"role": "user", "content": [
            {"type": "image", "image": image_path},
            {"type": "text",  "text": PROMPT_OCR},
        ]},
    ], has_image=True)
    jp_parsed, jp_err = _safe_parse(s1_raw)

    if jp_parsed is None:
        return {"japanese": None, "english": None, "ingredients": "",
                "latency_s": s1_lat, "error": f"OCR parse failed: {jp_err}",
                "s1_raw": s1_raw, "s2_raw": ""}

    # Stage 2: Translate
    ocr_str = json.dumps(jp_parsed, ensure_ascii=False, indent=2)
    prompt  = PROMPT_TRANSLATE_TEMPLATE.replace("{ocr_json}", ocr_str)
    s2_raw, s2_lat, _ = _generate([
        {"role": "system", "content": SYSTEM_TRANSLATE},
        {"role": "user",   "content": [{"type": "text", "text": prompt}]},
    ], has_image=False)
    en_parsed, en_err = _safe_parse(s2_raw)
    english = (en_parsed or {}).get("english") or en_parsed or {}

    # Fix 1: translate any remaining Japanese allergen terms
    en_alg = english.get("allergens") or {}
    if isinstance(en_alg, dict):
        en_alg["items"] = [JP_ALLERGEN_MAP.get(str(i).strip(), str(i))
                           for i in (en_alg.get("items") or [])]
        english["allergens"] = en_alg
    jp_alg_items = (jp_parsed.get("allergens") or {}).get("items") or []
    translated_jp = [JP_ALLERGEN_MAP.get(str(i).strip(), str(i)) for i in jp_alg_items]

    # Fix 2+3: bracket-aware ingredient flattening
    raw_items = english.get("ingredients") or []
    if isinstance(raw_items, list):
        split_items = [t for item in raw_items for t in _smart_split(str(item))]
    elif isinstance(raw_items, dict):
        split_items = [t for item in (raw_items.get("items") or []) for t in _smart_split(str(item))]
    else:
        split_items = _smart_split(str(raw_items))

    # Strip product name if it leaked as first ingredient
    # (model sometimes copies 名称 value before 原材料名 content)
    en_pname = (english.get("product_name") or "").strip().lower()
    if en_pname and split_items:
        first = split_items[0].strip().lower()
        if first == en_pname or en_pname in first:
            split_items = split_items[1:]

    ingredients_flat = ", ".join(split_items)

    # Append allergen terms: only use en_alg.items (which Stage 1 now filters to
    # in-product allergens only, excluding cross-contact 共通設備 declarations).
    # Do NOT fall back to translated_jp — that can include cross-contact allergens
    # that the model extracted from the shared-equipment warning sentence.
    def _is_english(s): return bool(str(s).strip()) and all(ord(c) < 0x3000 for c in str(s))
    existing_lower = {t.strip().lower() for t in ingredients_flat.split(",") if t.strip()}
    in_product_allergens = en_alg.get("items") or []
    new_terms = [t for t in dict.fromkeys(in_product_allergens)
                 if _is_english(t) and t.lower() not in existing_lower]
    if new_terms:
        ingredients_flat = (ingredients_flat + ", " + ", ".join(new_terms)).strip(", ")

    # Post-process: X/Y ingredient names → X (Y) for readability
    # e.g. "Miso/Soy Sauce" → "Miso (Soy Sauce)", "Starch/Modified Starch" → "Starch (Modified)"
    def _fix_slash(token):
        if "/" not in token:
            return token
        # Don't touch colour codes like Red #22
        if re.search(r"#\d", token):
            return token
        parts = token.split("/", 1)
        return f"{parts[0].strip()} ({parts[1].strip()})"

    ingredients_flat = ", ".join(_fix_slash(t) for t in ingredients_flat.split(", ") if t.strip())

    # Deduplicate: remove items that are an exact case-insensitive duplicate
    seen, deduped = set(), []
    for tok in ingredients_flat.split(", "):
        key = tok.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(tok.strip())
    ingredients_flat = ", ".join(deduped)

    # Fix 4: strip secondary bracketed values from nutrition strings
    nut_en = english.get("nutrition") or {}
    for k, v in nut_en.items():
        if isinstance(v, str):
            nut_en[k] = re.sub(r"\s*\(.*?\)", "", v).strip()
    english["nutrition"] = nut_en

    # Fix 4b: if Stage 2 returned null nutrition but Stage 1 had values, extract numbers directly
    jp_nut = (jp_parsed.get("nutrition") or {})
    en_nut_has_data = any(
        v is not None and str(v).strip() not in ("", "null")
        for k, v in nut_en.items() if k != "basis"
    )
    if not en_nut_has_data and jp_nut:
        # Directly extract numeric values from JP OCR (units stripped, numbers kept)
        fallback = {}
        for field in ["calories","protein","fat","saturated_fat","carbohydrate","sugar","fibre","salt"]:
            jp_val = jp_nut.get(field)
            if jp_val is None:
                fallback[field] = None
                continue
            s = str(jp_val).strip()
            s = re.sub(r"^うち\s*", "", s)   # strip うち prefix
            s = re.sub(r"\s*\(.*?\)", "", s)  # strip brackets
            m = re.search(r"-?\d+\.?\d*", s)
            fallback[field] = float(m.group()) if m else None
        # Translate basis
        basis_jp = jp_nut.get("basis") or ""
        fallback["basis"] = basis_jp  # will be processed by Fix 5
        english["nutrition"] = fallback
        nut_en = fallback

    # Fix 5: basis translation (Japanese → English)
    basis = nut_en.get("basis") or ""
    if basis and any(ord(c) > 0x3000 for c in basis):
        b = re.sub(r"当[たり]+|当り|あたり", "", basis)
        b = re.sub(r"コップ\s*(\d+)\s*杯", r"\1 cup", b)
        b = re.sub(r"(\d+)\s*袋", r"\1 bag", b)
        b = re.sub(r"(\d+)\s*本", r"\1 piece", b)
        b = re.sub(r"(\d+)\s*枚", r"\1 slice", b)
        b = re.sub(r"(\d+)\s*個", r"\1 piece", b)
        b = re.sub(r"(\d+)\s*食", r"\1 serving", b)
        b = re.sub(r"100\s*g", "per 100g", b)
        b = re.sub(r"[\u3000-\u9fff\uff00-\uffef]+", " ", b).strip()
        b = re.sub(r"\s+", " ", b).strip(" ·-")
        if b:
            nut_en["basis"] = b
            english["nutrition"] = nut_en

    # Fix 6: strip うち prefix from sugar/other nutrition values
    for field in ["sugar", "saturated_fat", "fibre"]:
        v = nut_en.get(field)
        if isinstance(v, str) and "うち" in v:
            nut_en[field] = re.sub(r"^うち\s*", "", v).strip()

    # Fix 7: saturated_fat sanity checks
    # (a) cannot exceed total fat
    # (b) if ratio satf/fat < 1%, almost certainly hallucinated (real foods always ≥1%)
    def _to_num(v):
        if v is None: return None
        try:
            m = re.search(r"\d+\.?\d*", str(v))
            return float(m.group()) if m else None
        except: return None

    fat_v  = _to_num(nut_en.get("fat"))
    satf_v = _to_num(nut_en.get("saturated_fat"))
    if satf_v is not None:
        bad = False
        if fat_v is not None and satf_v > fat_v:
            bad = True  # impossible: satf > fat
        elif fat_v == 0.0 and satf_v > 0:
            bad = True  # impossible: fat=0 but satf>0
        elif fat_v is not None and fat_v > 1.0 and satf_v < fat_v * 0.01:
            bad = True  # suspiciously tiny ratio (<1%) → hallucinated
        if bad:
            nut_en["saturated_fat"] = None
            english["nutrition"] = nut_en

    return {
        "japanese":    jp_parsed,
        "english":     english,
        "ingredients": ingredients_flat,
        "latency_s":   round(s1_lat + s2_lat, 2),
        "error":       en_err if en_parsed is None else None,
        "s1_raw":      s1_raw,
        "s2_raw":      s2_raw,
    }