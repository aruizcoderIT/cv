# cv.py — Cribador de VENTA TELEFÓNICA (solo PDF multi‑CV)
# --------------------------------------------------------
# - Acepta uno o varios PDF; cada PDF puede traer varios CV (InfoJobs).
# - Separa por candidato usando cabecera InfoJobs + nombre válido.
# - PUNTUACIÓN = SOLO señales de venta telefónica (nada de inglés ni estabilidad).
# - Inglés y estabilidad van en columnas aparte, no afectan a la puntuación.
# - Tabla ordenable/filtrable y exportable a CSV.

import io, os, re
import pdfplumber
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cribado Venta Telefónica (PDF)", layout="wide")
st.title("📞 Cribador de VENTA TELEFÓNICA — PDF multi‑CV")

# =========================
# Detección de cabecera y nombre (split por CV)
# =========================

HEADER_HINTS = ["datos del candidato", "ajuste nota", "% ajuste nota", "nota: 40/40"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

JOB_HINTS = [
    "teleoperador", "teleoperadora", "comercial", "administr", "limpiador", "limpiadora",
    "camarer", "dependient", "director", "directora", "encargad", "representante",
    "asesor", "asesora", "agente", "grabador", "operari", "auxiliar", "manager",
    "tecnico", "técnico", "tecnica", "técnica"
]
SECTION_HINTS = {"experiencia", "estudios", "idiomas", "conocimientos", "otros", "perfil", "resumen"}
CONNECTORS = {"de", "del", "la", "las", "los", "y", "da", "do", "dos"}

def _looks_like_name(line: str) -> bool:
    if not line: return False
    l = line.strip()
    if "@" in l or any(ch.isdigit() for ch in l):
        return False
    w = [t for t in re.split(r"\s+", l) if t]
    if len(w) < 2 or len(w) > 5:
        return False
    lw = l.lower()
    if any(h in lw for h in JOB_HINTS):      # evita “Limpiadora”, “Administración”, etc.
        return False
    if any(h in lw for h in SECTION_HINTS):
        return False
    for t in w:
        t_clean = re.sub(r"[’'´`-]", "", t)
        if t_clean.lower() in CONNECTORS:
            continue
        if not re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+$", t_clean):
            return False
    return True

def _normalize_name(line: str) -> str:
    parts = [p for p in re.split(r"\s+", line.strip()) if p]
    norm = []
    for p in parts:
        base = re.sub(r"[’'´`]", "", p)
        if base.lower() in CONNECTORS:
            norm.append(base.lower())
        else:
            norm.append(base[:1].upper() + base[1:].lower())
    return " ".join(norm)

def _extract_name_from_page(text: str) -> str | None:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    head = lines[:10]
    for ln in head:
        if _looks_like_name(ln):
            return _normalize_name(ln)
    return None

def _is_header_page(text: str) -> tuple[bool, str | None]:
    if not text:
        return False, None
    t = text.lower()
    has_marker = any(h in t for h in HEADER_HINTS) or EMAIL_RE.search(text) is not None
    if not has_marker:
        return False, None
    name = _extract_name_from_page(text)
    return (name is not None), name

def split_pdf_by_candidates(raw_bytes: bytes):
    """
    Devuelve [{"name":..., "text":..., "pages":[...]}] agrupando páginas hasta una nueva cabecera válida.
    """
    out = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        current = {"name": None, "text": "", "pages": []}
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            is_header, name = _is_header_page(txt)
            if is_header:
                if current["text"].strip():
                    out.append(current)
                    current = {"name": None, "text": "", "pages": []}
                current["name"] = name
            current["text"] += (txt + "\n")
            current["pages"].append(i)
        if current["text"].strip():
            out.append(current)
    for c in out:
        if not c["name"]:
            c["name"] = _extract_name_from_page(c["text"]) or "(sin nombre)"
    return out

def pdf_multi_to_df(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.read()
    chunks = split_pdf_by_candidates(raw)
    rows = []
    for c in chunks:
        rows.append({
            "nombre": c["name"],
            "_texto": c["text"][:8000],
            "_pdf_paginas": ",".join(str(p+1) for p in c["pages"]),
            "_fuente_pdf": uploaded_file.name,
        })
    return pd.DataFrame(rows)

# =========================
# Detección de INGLÉS (columna aparte)
# =========================

def english_level(texto: str) -> str:
    t = (texto or "").lower()
    if "ingl" not in t and "english" not in t:
        return ""
    levels = []
    if re.search(r"\b(c-?2|nativo)\b", t): levels.append("C2/Nativo")
    if re.search(r"\bc1\b", t):            levels.append("C1")
    if re.search(r"\bb2\b", t):            levels.append("B2")
    if re.search(r"\bb1\b|\bintermedio\b", t): levels.append("B1/Intermedio")
    if re.search(r"\bavanzad", t):         levels.append("Avanzado")
    if re.search(r"\bbas(ic|ico)\b|\ba2\b|\ba1\b", t): levels.append("Básico/A1-A2")
    if not levels: return "Mención sin nivel"
    # Si hay varios, devolvemos el más alto considerando orden:
    order = ["C2/Nativo","C1","B2","B1/Intermedio","Avanzado","Básico/A1-A2","Mención sin nivel"]
    for lvl in order:
        if lvl in levels:
            return lvl
    return ", ".join(levels)

# =========================
# SCORE: SOLO VENTA TELEFÓNICA
# =========================
# Regla: el score solo suma por señales de VENTA TELEFÓNICA.
# English y estabilidad quedan fuera del score.

PHONE_STRONG = {
    "venta telefónica": 4, "venta telefonica": 4, "televenta": 4,
    "venta por teléfono": 4, "ventas por teléfono": 4, "ventas telefónicas": 4,
    "cierre telefónico": 5, "cierre por teléfono": 5,  # cierre explícito por teléfono
}
PHONE_ACTIVITY = {
    "llamadas salientes": 3, "emisión de llamadas": 3, "emision de llamadas": 3, "outbound": 3,
    "llamadas entrantes": 2, "inbound": 2,
    "venta fría": 3, "venta en frío": 3, "venta fria": 3, "cold calling": 3,
    "retención de clientes": 2, "fidelización": 2, "fidelizacion": 2,
    "venta cruzada": 2, "upselling": 2, "cross selling": 2, "cross-selling": 2,
}

# Técnicas/negociación: SOLO suman si hay contexto telefónico en el CV
PHONE_TECH = {
    "cierre de ventas": 3, "manejo de objeciones": 3, "objeciones": 2,
    "negociación": 3, "negociacion": 3,
    "técnicas de cierre": 3, "tecnicas de cierre": 3,
    "one call close": 3, "one‑call close": 3, "trial close": 2,
    "spin selling": 2, "spin": 2, "sandler": 2, "nepq": 2, "aida": 2, "bant": 2,
}

# Penalizaciones ligeras si NO hay señales de cierre telefónico
ROLE_PENALTIES = {
    "teleoperador": -2, "teleoperadora": -2, "call center": -2,
    "atención al cliente": -1, "atencion al cliente": -1,
}

def has_phone_context(t: str) -> bool:
    keys = list(PHONE_STRONG.keys()) + list(PHONE_ACTIVITY.keys())
    return any(k in t for k in keys)

def score_venta_telefonica(texto: str) -> dict:
    t = (texto or "").lower()
    score = 0
    tags = []

    # Señales telefónicas core
    for k, w in PHONE_STRONG.items():
        if k in t: score += w; tags.append(k)
    for k, w in PHONE_ACTIVITY.items():
        if k in t: score += w; tags.append(k)

    # Técnicas/negociación SOLO si hay contexto telefónico
    if has_phone_context(t):
        for k, w in PHONE_TECH.items():
            if k in t: score += w; tags.append(k)

    # Penalizaciones solo si NO hay señales claras de cierre telefónico
    has_close_phone = ("cierre telefónico" in t) or ("cierre por teléfono" in t) or ("venta telefónica" in t) or ("venta telefonica" in t) or ("televenta" in t)
    if not has_close_phone:
        for k, w in ROLE_PENALTIES.items():
            if k in t: score += w; tags.append(f"-{k}")

    return {"score_tel": score, "tags": ", ".join(sorted(set(tags)))}

# =========================
# Estabilidad (columnas informativas)
# =========================

RE_YEARS = re.compile(r"(\d+)\s*años?", re.IGNORECASE)
RE_MONTHS = re.compile(r"(\d+)\s*meses?", re.IGNORECASE)
RE_DUR_PARENS = re.compile(r"\(([^)]*años?[^)]*|[^)]*meses?)\)")

def estimate_stability(texto: str):
    t = (texto or "").lower()
    meses_totales = 0; estables = 0; muy_cortos = 0
    for m in RE_YEARS.finditer(t):  meses_totales += int(m.group(1)) * 12
    for m in RE_MONTHS.finditer(t): meses_totales += int(m.group(1))
    for blk in RE_DUR_PARENS.findall(t):
        y = RE_YEARS.search(blk); m = RE_MONTHS.search(blk)
        total_blk = (int(y.group(1))*12 if y else 0) + (int(m.group(1)) if m else 0)
        if total_blk >= 12: estables += 1
        elif 0 < total_blk < 3: muy_cortos += 1
    # heurísticos suaves:
    if re.search(r"(\b1\s*año\b|\b12\s*meses\b)", t): estables += 1
    if re.search(r"\b(1|2)\s*mes(es)?\b", t):        muy_cortos += 1
    return meses_totales, estables, muy_cortos

# =========================
# UI: carga, split, score, tabla
# =========================

with st.sidebar:
    st.header("📂 Subida de PDF(s)")
    files = st.file_uploader(
        "Sube PDF(s) de InfoJobs (un PDF puede contener varios CVs)",
        type=["pdf"],
        accept_multiple_files=True
    )
    st.caption("Separa por cabecera InfoJobs + nombre. Score = solo venta telefónica.")

if not files:
    st.info("Sube al menos un PDF para empezar.")
    st.stop()

bloques = []
for f in files:
    try:
        df_pdf = pdf_multi_to_df(f)          # 1 fila por candidato real
        bloques.append(df_pdf)
    except Exception as e:
        st.error(f"Error procesando {f.name}: {e}")

if not bloques:
    st.error("No se han podido procesar PDFs válidos.")
    st.stop()

df = pd.concat(bloques, ignore_index=True, sort=False)

# Score SOLO telefónico + columnas adicionales
sc = df["_texto"].apply(score_venta_telefonica)
df["Puntuación"] = [x["score_tel"] for x in sc]
df["matches"]     = [x["tags"]      for x in sc]

eng = df["_texto"].apply(english_level)
df["nivel_ingles"] = eng

stab = df["_texto"].apply(estimate_stability)
df["meses_totales"] = [s[0] for s in stab]
df["puestos_estables(>=12m)"] = [s[1] for s in stab]
df["puestos_muy_cortos(<3m)"] = [s[2] for s in stab]

st.success(f"✅ {len(df)} candidatos detectados y puntuados (score = venta telefónica)")

# Controles
min_s = int(df["Puntuación"].min()) if "Puntuación" in df else 0
max_s = int(df["Puntuación"].max()) if "Puntuación" in df else 0
def_val = min(min_s + 5, max_s) if max_s > min_s else min_s

with st.sidebar:
    umbral = st.slider(
        "Puntuación mínima (venta telefónica)",
        min_value=min_s,
        max_value=max_s if max_s > min_s else min_s+1,
        value=def_val
    )
    ordenar_por = st.selectbox(
        "Ordenar por",
        options=["Puntuación", "nombre", "nivel_ingles", "meses_totales",
                 "puestos_estables(>=12m)", "puestos_muy_cortos(<3m)", "_fuente_pdf", "_pdf_paginas"],
        index=0
    )
    asc = st.checkbox("Ascendente", value=False)
    top_n = st.number_input("Ver primeros N", min_value=50, max_value=5000, value=300, step=50)

filtrado = df[df["Puntuación"] >= umbral].sort_values(by=ordenar_por, ascending=asc)

st.subheader(f"📋 Candidatos (score telefónico ≥ {umbral})")
cols_pre = ["nombre","Puntuación","nivel_ingles","matches","meses_totales",
            "puestos_estables(>=12m)","puestos_muy_cortos(<3m)","_pdf_paginas","_fuente_pdf"]
cols_show = [c for c in cols_pre if c in filtrado.columns] + [c for c in filtrado.columns if c not in cols_pre]
st.dataframe(filtrado[cols_show].head(int(top_n)), use_container_width=True)

# Exportar
csv_out = filtrado.to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Descargar CSV", csv_out, "candidatos_filtrados.csv", "text/csv")

st.caption("La puntuación solo refleja venta telefónica. Inglés y estabilidad no afectan al score; quedan como columnas.")
