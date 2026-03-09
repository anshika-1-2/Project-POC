"""
app.py  —  Japanese Food Label Allergen Scanner
Wide layout · DRI Calculator · Persistent inputs · Polished UI

Run:  streamlit run app.py
"""

import tempfile
from pathlib import Path

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from services.detection import analyze_ingredients, JP_MANDATORY
from services.storage    import save_image, save_record
from services.dri        import calculate_dri
from services.diet       import classify_diet

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Food Label Allergen Scanner",
    page_icon="🍱",
    layout="wide",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stSidebar"] { background:#f4f6fb; }
[data-testid="stSidebar"] .block-container { padding-top: 1rem; }

/* Ingredient chip */
.chip {
  display:inline-block; background:#eef2f7; color:#2d3748;
  border-radius:6px; padding:2px 9px; margin:2px; font-size:12.5px;
}

/* Allergen / additive pills */
.pill {
  display:inline-block; border-radius:20px;
  padding:3px 13px; margin:3px; font-size:13px; font-weight:500;
}
.pill-red    { background:#fde8e8; color:#c0392b; }
.pill-yellow { background:#fef9e2; color:#9a6600; }
.pill-orange { background:#fef0e0; color:#c05000; }
.pill-green  { background:#e6f9ee; color:#1a6e3a; }
.pill-blue   { background:#e8f0fe; color:#1a56db; }

/* Section cards */
.sec-header {
  font-size:15px; font-weight:700; color:#1e293b;
  letter-spacing:.02em; margin:14px 0 6px 0;
}

/* Metric tweak */
[data-testid="stMetricValue"] { font-size:28px !important; font-weight:700 !important; }
[data-testid="stMetricLabel"] { font-size:12px !important; color:#64748b !important; }

/* Diet cards */
.diet-card {
    border-radius: 14px;
    padding: 14px 18px;
    margin-bottom: 10px;
    border-left: 5px solid;
    position: relative;
    transition: box-shadow .15s;
}
.diet-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
.diet-yes    { background:#f0faf4; border-color:#22c55e; }
.diet-no     { background:#fff1f2; border-color:#ef4444; }
.diet-caution{ background:#fffbeb; border-color:#f59e0b; }
.diet-uncertain{ background:#f0f4ff; border-color:#6366f1; }
.diet-icon   { font-size:22px; margin-right:8px; vertical-align:middle; }
.diet-title  { font-size:15px; font-weight:700; vertical-align:middle; }
.diet-label  { font-size:12px; font-weight:600; margin-left:6px;
               padding:1px 8px; border-radius:10px; vertical-align:middle; }
.diet-yes   .diet-label  { background:#dcfce7; color:#166534; }
.diet-no    .diet-label  { background:#fee2e2; color:#991b1b; }
.diet-caution .diet-label{ background:#fef3c7; color:#92400e; }
.diet-uncertain .diet-label{ background:#e0e7ff; color:#3730a3; }
.diet-reason { font-size:12px; color:#64748b; margin-top:5px; padding-left:32px; }
.diet-reason .neg { color:#dc2626; margin-right:6px; }
.diet-reason .pos { color:#16a34a; margin-right:6px; }
</style>
""", unsafe_allow_html=True)


# ── Session-state initialisation ─────────────────────────────────────────────
DEFAULTS = dict(
    result       = None,
    dri          = None,
    # Persist sidebar inputs so they survive reruns
    sb_name      = "",
    sb_allergen  = "",
    sb_sex       = "Female",
    sb_age       = 25,
    sb_height    = 165.0,
    sb_weight    = 60.0,
    sb_activity  = "Active",
    sb_pregnancy = "None",
)
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🍱 Food Label Scanner")
    st.divider()

    # ── User identity ─────────────────────────────────────────────────────────
    st.markdown("### 👤 Your Profile")

    st.session_state.sb_name = st.text_input(
        "Name", value=st.session_state.sb_name, placeholder="e.g. Anshika"
    )
    st.session_state.sb_allergen = st.text_input(
        "Your allergen (optional)",
        value=st.session_state.sb_allergen,
        placeholder="e.g. peanut, milk, wheat",
        help="Allergen you are sensitive to — leave blank if none.",
    ).strip().lower()

    st.divider()

    # ── DRI Calculator ────────────────────────────────────────────────────────
    st.markdown("### 🧮 DRI Calculator")
    st.caption("USDA Dietary Reference Intakes")

    c1, c2 = st.columns(2)
    with c1:
        st.session_state.sb_sex = st.selectbox(
            "Sex", ["Female", "Male"],
            index=["Female","Male"].index(st.session_state.sb_sex),
        )
    with c2:
        st.session_state.sb_age = st.number_input(
            "Age (yr)", min_value=19, max_value=100,
            value=st.session_state.sb_age, step=1,
        )

    c3, c4 = st.columns(2)
    with c3:
        st.session_state.sb_height = st.number_input(
            "Height (cm)", min_value=100.0, max_value=250.0,
            value=st.session_state.sb_height, step=0.5,
        )
    with c4:
        st.session_state.sb_weight = st.number_input(
            "Weight (kg)", min_value=20.0, max_value=300.0,
            value=st.session_state.sb_weight, step=0.5,
        )

    activity_opts = ["Sedentary", "Low Active", "Active", "Very Active"]
    st.session_state.sb_activity = st.selectbox(
        "Activity Level", activity_opts,
        index=activity_opts.index(st.session_state.sb_activity),
        help="Sedentary=desk job · Low Active=light walk · Active=regular gym · Very Active=daily hard training",
    )

    preg_opts = ["None","1st trimester","2nd trimester","3rd trimester",
                 "Breastfeeding 0-6 months","Breastfeeding 7-12 months"]
    st.session_state.sb_pregnancy = st.selectbox(
        "Pregnancy / Breastfeeding", preg_opts,
        index=preg_opts.index(st.session_state.sb_pregnancy),
    )

    if st.button("⚡ Calculate DRI", type="primary", use_container_width=True):
        pg = st.session_state.sb_pregnancy
        pregnancy = pg if "trimester"    in pg else "None"
        lactation = pg.replace("Breastfeeding ","") if "Breastfeeding" in pg else "None"
        st.session_state.dri = calculate_dri(
            sex       = st.session_state.sb_sex.lower(),
            age       = int(st.session_state.sb_age),
            weight_kg = float(st.session_state.sb_weight),
            height_cm = float(st.session_state.sb_height),
            activity  = st.session_state.sb_activity,
            pregnancy = pregnancy,
            lactation = lactation,
        )
        st.success("DRI calculated ✓")

    st.divider()
    st.caption("All scans are saved with your name, allergen, DRI profile, and detected results.")

    # ── Show saved inputs summary if DRI calculated ───────────────────────────
    if st.session_state.dri:
        d = st.session_state.dri["inputs"]
        st.markdown(
            f"**Saved profile:**  \n"
            f"{d['sex'].title()} · {d['age']} yrs · {d['height_cm']} cm · "
            f"{d['weight_kg']} kg · {d['activity']}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def bmi_color(cls):
    return {"Underweight":"#3B9AE1","Normal weight":"#2EC4B6",
            "Overweight":"#F4A261","Obese":"#E63946"}.get(cls,"#888")

def confidence_badge(score):
    if score >= 80: return f"🟢 {score}%"
    if score >= 50: return f"🟡 {score}%"
    return f"🔴 {score}%"

def compute_confidence(jp, en):
    s = {}
    pname = (jp or {}).get("product_name")
    s["Product Name"] = 100 if pname and str(pname).strip() else 0

    ing   = (jp or {}).get("ingredients") or {}
    items = ing.get("items") or [] if isinstance(ing, dict) else []
    s["Ingredients"] = 100 if len(items)>=5 else 70 if len(items)>=2 else 40 if len(items)==1 else 0

    alg = (jp or {}).get("allergens")
    if   alg is None:        s["Allergens"] = 0
    elif isinstance(alg, dict):
        st2, ai = alg.get("style"), alg.get("items") or []
        s["Allergens"] = 100 if st2 and ai else 60 if st2 else 30
    else: s["Allergens"] = 30

    nut    = (jp or {}).get("nutrition") or {}
    filled = sum(1 for k in ["calories","protein","fat","carbohydrate","salt"] if nut.get(k))
    s["Nutrition"] = round(filled/5*100)
    return {"fields": s, "overall": round(sum(s.values())/len(s))}

# ── Nutrition value limits (physiologically plausible per serving) ────────────
_NUT_LIMITS = {
    # (label, max_g_or_kcal, mg_threshold)
    # If value > mg_threshold, it was likely in mg — auto-convert to g
    "Calories":     (9999, None),   # kcal — no mg conversion
    "Protein":      (200,  None),
    "Fat":          (200,  None),
    "Carbohydrate": (500,  None),
    "Salt":         (10,   100),    # >100 almost certainly mg, divide by 1000
}

def _strip_to_float(val) -> float | None:
    """
    Convert a nutrition value to float, stripping any embedded unit text.
    Handles: 76, "76", "76kcal", "6.2g", "3.9 g", "227mg", etc.
    Also detects mg values and converts to g.
    Returns (numeric_value, is_mg) tuple.
    """
    import re as _re
    if val is None:
        return None, False
    s = str(val).strip()
    is_mg = bool(_re.search(r"mg", s, _re.IGNORECASE))
    try:
        return float(s), is_mg
    except (ValueError, TypeError):
        m = _re.search(r"-?\d+\.?\d*", s)
        if m:
            try:
                return float(m.group()), is_mg
            except ValueError:
                pass
    return None, is_mg

def _clamp_nutrition(label: str, value: float, is_mg: bool) -> tuple[float, str]:
    """
    Apply unit sanity checks. Returns (corrected_value, unit_str).
    If value was in mg, converts to g. If still implausibly large, flags it.
    """
    unit = "kcal" if label == "Calories" else "g"
    if label == "Calories":
        return value, unit
    # Auto-convert mg → g
    if is_mg:
        value = value / 1000.0
    # Heuristic: if salt > 10g per serving, almost certainly a unit error → treat as mg
    elif label in _NUT_LIMITS:
        _, mg_thresh = _NUT_LIMITS[label]
        if mg_thresh and value > mg_thresh:
            value = value / 1000.0
    return round(value, 3), unit

def parse_nutrition(english):
    nut = (english or {}).get("nutrition") or {}
    if not nut: return None

    # Accept both long-form keys (calories_kcal, protein_g) and
    # short-form keys (calories, protein) that Stage 2 sometimes returns
    def _get(nut, *keys):
        for k in keys:
            v = nut.get(k)
            if v is not None:
                return v
        return None

    field_map = [
        (["calories_kcal", "calories"],              "Calories"),
        (["protein_g",     "protein"],               "Protein"),
        (["fat_g",         "fat"],                   "Fat"),
        (["carbohydrate_g","carbohydrate","carbs"],   "Carbohydrate"),
        (["salt_g",        "salt","sodium_g","sodium"],"Salt"),
    ]
    rows = []
    for keys, label in field_map:
        val = _get(nut, *keys)
        if val is None: continue
        numeric, is_mg = _strip_to_float(val)
        if numeric is not None:
            corrected, unit = _clamp_nutrition(label, numeric, is_mg)
            rows.append({"Nutrient": label, "Amount": corrected, "Unit": unit})
        else:
            unit = "kcal" if label == "Calories" else "g"
            rows.append({"Nutrient": label, "Amount": str(val), "Unit": unit})
    return {"rows": rows, "basis": nut.get("basis")} if rows else None

def nutrition_pie(rows):
    keys = {"Protein","Fat","Carbohydrate"}
    labels,values = [],[]
    for r in rows:
        if r["Nutrient"] not in keys: continue
        try: v = float(r["Amount"])
        except: v = 0.0
        if v>0: labels.append(r["Nutrient"]); values.append(v)
    if not labels: return None
    colors = ["#4C9BE8","#F4A261","#2EC4B6"][:len(labels)]
    fig,ax = plt.subplots(figsize=(3.2,3.2),facecolor="none")
    _,_,autotexts = ax.pie(values,labels=None,autopct="%1.1f%%",colors=colors,
                           startangle=140,wedgeprops={"edgecolor":"white","linewidth":1.5})
    for at in autotexts: at.set_fontsize(10);at.set_color("white");at.set_fontweight("bold")
    patches = [mpatches.Patch(color=c,label=f"{l} ({v:.1f}g)") for c,l,v in zip(colors,labels,values)]
    ax.legend(handles=patches,loc="lower center",bbox_to_anchor=(0.5,-0.22),
              ncol=len(labels),fontsize=8,frameon=False)
    ax.set_title("Macronutrients",fontsize=10,pad=6)
    fig.tight_layout(); return fig

def dri_bar_chart(nut_rows, dri_raw):
    pairs = [("Calories","calories","kcal"),("Protein","protein_g","g"),
             ("Fat","fat_max_g","g"),("Carbohydrate","carb_max_g","g"),
             ("Salt","sodium_g","g")]
    lv_map = {r["Nutrient"]: r["Amount"] for r in nut_rows if isinstance(r.get("Amount"),float)}
    names,pcts = [],[]
    for nutrient,dri_key,_ in pairs:
        lv = lv_map.get(nutrient); dv = dri_raw.get(dri_key)
        if lv is not None and dv:
            try: names.append(nutrient); pcts.append(round(float(lv)/float(dv)*100,1))
            except: pass
    if not names: return None
    colors = ["#2EC4B6" if p<=25 else "#4C9BE8" if p<=50 else "#F4A261" if p<=80 else "#E63946"
              for p in pcts]
    fig,ax = plt.subplots(figsize=(4.5,3.0),facecolor="none")
    bars = ax.barh(names,pcts,color=colors,edgecolor="none",height=0.42)
    ax.axvline(100,color="#E63946",linestyle="--",linewidth=1.1,alpha=0.6,label="100% DRI")
    for bar,pct in zip(bars,pcts):
        ax.text(bar.get_width()+0.8,bar.get_y()+bar.get_height()/2,
                f"{pct}%",va="center",fontsize=9,color="#333")
    ax.set_xlabel("% of Daily DRI",fontsize=9)
    ax.set_xlim(0,max(pcts+[100])*1.28)
    ax.tick_params(labelsize=9)
    ax.spines[["top","right"]].set_visible(False)
    ax.legend(fontsize=8,frameon=False)
    fig.tight_layout(); return fig


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — two-column layout
# ══════════════════════════════════════════════════════════════════════════════
left_col, right_col = st.columns([11, 9], gap="large")


# ────────────────────────────────────────────────────────────────────────────
# LEFT  — Scanner
# ────────────────────────────────────────────────────────────────────────────
with left_col:
    st.markdown("## 📸 Scan a Food Label")

    uploaded = st.file_uploader(
        "Drop a food packet photo here",
        type=["jpg","jpeg","png","webp"],
        label_visibility="collapsed",
    )
    if uploaded:
        st.image(uploaded, use_container_width=True)

    user_name     = st.session_state.sb_name.strip()
    user_allergen = st.session_state.sb_allergen

    scan_ready = bool(uploaded and user_name)
    scan_clicked = st.button("🔍 Scan Label", type="primary", disabled=not scan_ready)

    if not uploaded:
        st.info("📂 Upload a Japanese food label image above.")
    elif not user_name:
        st.warning("✏️ Enter your name in the sidebar first.")

    # ── Run scan ──────────────────────────────────────────────────────────────
    if scan_clicked and scan_ready:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tmp:
            tmp.write(uploaded.read()); tmp_path = tmp.name

        with st.spinner("🔄 Running OCR + translation…"):
            from services.ocr import extract_label
            ocr_result = extract_label(tmp_path)

        if ocr_result.get("error") and not ocr_result.get("ingredients"):
            st.error(f"OCR failed: {ocr_result['error']}")
        else:
            ingredients_text = ocr_result.get("ingredients","")
            english  = ocr_result.get("english") or {}
            japanese = ocr_result.get("japanese") or {}

            with st.spinner("🔬 Detecting allergens & additives…"):
                detection = analyze_ingredients(ingredients_text)

            en_nutrition = (ocr_result.get("english") or {}).get("nutrition")
            diet_result  = classify_diet(
                ingredients_flat = ingredients_text,
                nutrition        = en_nutrition,
                allergens        = detection["allergens"],
            )

            saved_img = save_image(tmp_path)
            user_id   = save_record(
                user_id            = "",
                user_name          = user_name,
                user_allergen      = user_allergen,
                image_path         = saved_img,
                ingredients        = ingredients_text,
                detected_allergens = detection["allergens"],
                detected_additives = detection["additives"],
                dri                = st.session_state.dri,
            )

            st.session_state.result = {
                "user_id":       user_id,
                "ocr":           ocr_result,
                "detection":     detection,
                "diet":          diet_result,
                "ingredients":   ingredients_text,
                "user_allergen": user_allergen,
                "confidence":    compute_confidence(japanese, english),
            }

    # ── Show results ──────────────────────────────────────────────────────────
    if st.session_state.result:
        r         = st.session_state.result
        detection = r["detection"]
        allergens = detection["allergens"]
        additives = detection["additives"]
        user_alg  = r["user_allergen"]
        ocr       = r["ocr"]
        english   = ocr.get("english") or {}
        japanese  = ocr.get("japanese") or {}
        conf      = r.get("confidence") or compute_confidence(japanese, english)

        st.divider()

        # Header row
        h1, h2, h3 = st.columns([3, 2, 2])
        with h1:
            pname = (english or {}).get("product_name") or "—"
            st.markdown(f"**{pname}**")
            st.caption(f"Scan `{r['user_id']}` · {ocr.get('latency_s','—')}s")
        with h2:
            st.metric("OCR Confidence", confidence_badge(conf["overall"]))
        with h3:
            total_flags = len(allergens) + len(additives)
            st.metric("Flags", f"{total_flags} item{'s' if total_flags!=1 else ''}")

        # Confidence breakdown
        with st.expander("📊 OCR Confidence Breakdown", expanded=False):
            ccols = st.columns(len(conf["fields"]))
            for col,(field,score) in zip(ccols, conf["fields"].items()):
                col.metric(field, confidence_badge(score))
            st.caption("🟢 ≥80% · 🟡 50-79% · 🔴 <50%")

        # ── Ingredients ───────────────────────────────────────────────────────
        with st.expander("🧾 Ingredients (English)", expanded=True):
            raw_items = english.get("ingredients") or []
            if isinstance(raw_items, list) and raw_items:
                chips = "".join(f'<span class="chip">{item}</span>' for item in raw_items)
                st.markdown(chips, unsafe_allow_html=True)
            elif r["ingredients"]:
                st.write(r["ingredients"])
            else:
                st.info("No ingredients extracted.")

        # ── Allergen alerts ───────────────────────────────────────────────────
        st.markdown('<p class="sec-header">⚠️ Allergen & Additive Alerts</p>', unsafe_allow_html=True)

        # User's own allergen
        user_alg_found = any(
            (user_alg in det or det in user_alg)
            for det in allergens
        ) if user_alg else False

        if user_alg and user_alg_found:
            st.error(f"🚨 **Your allergen detected: {user_alg.title()}** — this product contains it.", icon="🚨")

        # Always show mandatory allergens in the section even if they match user's allergen
        # (user's allergen gets the red banner above; mandatory/recommended are additional info)
        mandatory      = [a for a in allergens if a in JP_MANDATORY]
        other          = [a for a in allergens if a not in JP_MANDATORY]
        recommended    = [a for a in other if not (user_alg and (user_alg in a or a in user_alg))]

        acol1, acol2 = st.columns(2)
        with acol1:
            st.markdown("**🏷️ Allergens**")
            if mandatory:
                st.markdown("🔴 Mandatory (JP law)")
                st.markdown(
                    "".join(f'<span class="pill pill-red">{a.title()}</span>' for a in sorted(mandatory)),
                    unsafe_allow_html=True,
                )
            if recommended:
                st.markdown("🟡 Recommended")
                st.markdown(
                    "".join(f'<span class="pill pill-yellow">{a.title()}</span>' for a in sorted(recommended)),
                    unsafe_allow_html=True,
                )
            if not mandatory and not recommended and not user_alg_found:
                st.markdown('<span class="pill pill-green">✅ None detected</span>', unsafe_allow_html=True)
            elif user_alg_found and not mandatory and not recommended:
                st.markdown(
                    f'<span class="pill pill-blue">⚠️ {user_alg.title()} (shown above)</span>',
                    unsafe_allow_html=True,
                )

        with acol2:
            st.markdown("**🧪 Additives**")
            if additives:
                st.markdown(
                    "".join(
                        f'<span class="pill pill-orange">{aname}'
                        f'<span style="font-size:10px;opacity:.7"> #{aid}</span></span>'
                        for aid,aname in additives
                    ),
                    unsafe_allow_html=True,
                )
            else:
                st.markdown('<span class="pill pill-green">✅ None detected</span>', unsafe_allow_html=True)

        # ── Diet Classification ───────────────────────────────────────────────
        diet = r.get("diet") or {}
        if diet:
            st.markdown('<p class="sec-header">🥗 Diet Classification</p>', unsafe_allow_html=True)

            DIET_META = {
                "vegan":      ("🌱", "Vegan"),
                "vegetarian": ("🥚", "Vegetarian"),
                "keto":       ("🥑", "Keto"),
                "diabetic":   ("🩺", "Diabetic-Friendly"),
            }
            STATUS_CLASS = {
                "yes": "diet-yes", "no": "diet-no",
                "caution": "diet-caution", "uncertain": "diet-uncertain",
            }

            dcols = st.columns(2)
            for idx, (key, (icon, title)) in enumerate(DIET_META.items()):
                d = diet.get(key, {})
                status  = d.get("status", "uncertain")
                label   = d.get("label", "Unknown")
                reasons = d.get("reasons", [])
                positives = d.get("positives", [])
                css_cls = STATUS_CLASS.get(status, "diet-uncertain")

                reason_html = ""
                for r_txt in reasons[:2]:
                    reason_html += f'<span class="neg">✗</span>{r_txt}<br>'
                for p_txt in positives[:1]:
                    reason_html += f'<span class="pos">✓</span>{p_txt}<br>'

                html = (
                    f'<div class="diet-card {css_cls}">'
                    f'<span class="diet-icon">{icon}</span>'
                    f'<span class="diet-title">{title}</span>'
                    f'<span class="diet-label">{label}</span>'
                    f'<div class="diet-reason">{reason_html}</div>'
                    f'</div>'
                )
                with dcols[idx % 2]:
                    st.markdown(html, unsafe_allow_html=True)

        # ── Nutrition ─────────────────────────────────────────────────────────
        nut_data = parse_nutrition(english)
        if nut_data and nut_data["rows"]:
            st.markdown('<p class="sec-header">🥗 Nutrition Information</p>', unsafe_allow_html=True)
            if nut_data["basis"]:
                st.caption(f"Per serving: {nut_data['basis']}")

            nc1, nc2 = st.columns([1,1])
            with nc1:
                df = pd.DataFrame(nut_data["rows"])
                def _fmt_value(row):
                    amt, unit = row["Amount"], row["Unit"]
                    if isinstance(amt, float):
                        n = int(amt) if amt == int(amt) else round(amt, 2)
                        return f"{n} {unit}"
                    return str(amt)
                df["Value"] = df.apply(_fmt_value, axis=1)
                st.dataframe(df[["Nutrient","Value"]].set_index("Nutrient"), use_container_width=True)
            with nc2:
                fig = nutrition_pie(nut_data["rows"])
                if fig: st.pyplot(fig, use_container_width=True)
                else:   st.info("No macronutrient data for chart.")

            # DRI comparison
            if st.session_state.dri:
                st.markdown('<p class="sec-header">📊 This Product vs Your Daily DRI</p>', unsafe_allow_html=True)
                st.caption("What % of your estimated daily needs does this single product provide?")
                comp_fig = dri_bar_chart(nut_data["rows"], st.session_state.dri["_raw"])
                if comp_fig:
                    st.pyplot(comp_fig, use_container_width=True)
                    dri_raw   = st.session_state.dri["_raw"]
                    lv_map    = {r["Nutrient"]: r["Amount"] for r in nut_data["rows"]
                                 if isinstance(r.get("Amount"), float)}
                    comp_rows = []
                    sodium_g = dri_raw.get("sodium_g")
                    sodium_label = f"{sodium_g} g" if sodium_g else "1.5 g"
                    for nutrient,dkey,dlabel in [
                        ("Calories",    "calories",  f"{dri_raw.get('calories')} kcal"),
                        ("Protein",     "protein_g", f"{dri_raw.get('protein_g')} g"),
                        ("Fat",         "fat_max_g", f"{dri_raw.get('fat_max_g')} g max"),
                        ("Carbohydrate","carb_max_g",f"{dri_raw.get('carb_max_g')} g max"),
                        ("Salt",        "sodium_g",  sodium_label),
                    ]:
                        lv = lv_map.get(nutrient); dv = dri_raw.get(dkey)
                        if lv is not None and dv:
                            try:
                                pct = round(float(lv)/float(dv)*100, 1)
                                comp_rows.append({"Nutrient":nutrient,"In Product":f"{lv}",
                                                  "Daily DRI":dlabel,"% of Need":f"{pct}%"})
                            except: pass
                    if comp_rows:
                        st.dataframe(pd.DataFrame(comp_rows).set_index("Nutrient"),
                                     use_container_width=True)
                else:
                    st.info("Not enough nutrition data for comparison.")
            else:
                st.caption("💡 Calculate your DRI in the sidebar to compare this product against your daily needs.")
        else:
            st.caption("ℹ️ No nutrition data extracted from this label.")

        with st.expander("🔬 Raw OCR output (Japanese)", expanded=False):
            st.json(japanese or {})


# ────────────────────────────────────────────────────────────────────────────
# RIGHT  — DRI Profile
# ────────────────────────────────────────────────────────────────────────────
with right_col:
    st.markdown("## 🧮 Your DRI Profile")

    if not st.session_state.dri:
        st.markdown("""
        <div style="background:#f0f4ff;border-radius:12px;padding:20px 24px;
                    border:1px solid #c7d5f5;color:#2d3a6e;font-size:14px">
          <b>Fill in your details in the sidebar</b> and click
          <b>⚡ Calculate DRI</b> to see your personalised
          Dietary Reference Intake values — calories, macros,
          vitamins, and minerals.
        </div>
        """, unsafe_allow_html=True)

    else:
        dri  = st.session_state.dri
        inp  = dri["inputs"]
        bclr = bmi_color(dri["bmi_class"])

        # Profile summary banner
        st.markdown(
            f'<div style="background:#eef6ff;border-radius:10px;padding:10px 16px;'
            f'font-size:13px;color:#1e3a5f;margin-bottom:12px">'
            f'<b>{inp["sex"].title()}</b> · {inp["age"]} yrs · '
            f'{inp["height_cm"]} cm · {inp["weight_kg"]} kg · '
            f'<b>{inp["activity"]}</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # BMI + EER metrics
        m1, m2 = st.columns(2)
        with m1:
            st.metric("BMI", dri["bmi"])
            st.markdown(
                f'<span style="background:{bclr};color:white;border-radius:10px;'
                f'padding:2px 10px;font-size:12px;font-weight:600">{dri["bmi_class"]}</span>',
                unsafe_allow_html=True,
            )
        with m2:
            st.metric("Daily Calories", f"{dri['eer']:,} kcal")

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Macros ────────────────────────────────────────────────────────────
        with st.expander("🥦 Macronutrients", expanded=True):
            rows = [{"Nutrient":k,"Recommended / Day":v["value"],"Basis":v["note"]}
                    for k,v in dri["macros"].items()]
            st.dataframe(pd.DataFrame(rows).set_index("Nutrient"), use_container_width=True)

        # ── Vitamins ──────────────────────────────────────────────────────────
        with st.expander("💊 Vitamins", expanded=False):
            rows = []
            for name,vals in dri["vitamins"].items():
                ul = str(vals["ul"]) if vals["ul"] != "ND" else "—"
                rows.append({"Vitamin":name,"RDA/AI":vals["rda"],"UL":ul})
            st.dataframe(pd.DataFrame(rows).set_index("Vitamin"), use_container_width=True)

        # ── Minerals ──────────────────────────────────────────────────────────
        with st.expander("⚗️ Minerals", expanded=False):
            rows = []
            for name,vals in dri["minerals"].items():
                ul = str(vals["ul"]) if vals["ul"] != "ND" else "—"
                rows.append({"Mineral":name,"RDA/AI":vals["rda"],"UL":ul})
            st.dataframe(pd.DataFrame(rows).set_index("Mineral"), use_container_width=True)

        # ── What's saved note ─────────────────────────────────────────────────
        st.markdown(
            '<div style="background:#f0faf4;border-radius:8px;padding:10px 14px;'
            'font-size:12px;color:#1a5e36;margin-top:8px">'
            '✅ <b>This profile is saved with every scan</b> — sex, age, height, '
            'weight, activity, BMI, and estimated daily calories.'
            '</div>',
            unsafe_allow_html=True,
        )