# cv.py — Cribado de CLOSERS B2C (solo PDF, multi‑CV, nombres fiables)
# --------------------------------------------------------------------
# - Sube uno o varios PDF; cada PDF puede contener varios CV (InfoJobs).
# - Separa automáticamente POR CANDIDATO usando páginas-cabecera reales:
#     * Debe contener "Datos del candidato" o "% ajuste Nota"
#     * Debe existir una línea que PAREZCA NOMBRE (2-5 palabras, sin oficios)
# - Puntuación orientada a CLOSERS en B2C: cierre/negociación/objeciones/técnicas, KPIs,
#   venta directa/presencial/puerta fría, y nivel de INGLÉS.
# - Penaliza teleoperación/soporte y rotación muy corta.
# - Bonifica estabilidad razonable (>=12 meses).
# - Ordena/filtra y permite descargar CSV.

import io, os, re
import pdfplumber
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cribado CLOSERS B2C (PDF)", layout="wide")
st.title("🎯 Cribador de CLOSERS B2C — PDF multi‑CV")

# =========================
# Config de scoring (B2C)
# =========================

# Fuerte en cierre/negociación/objeciones/técnicas
CLOSER_STRONG = {
    "cierre de ventas": 4, "cerrador": 4,
    "técnicas de cierre": 4, "tecnicas de cierre": 4,
    "manejo de objeciones": 3, "objeciones": 3,
    "negociación": 4, "negociacion": 4,
    "venta consultiva": 3,
    "one call close": 3, "one‑call close": 3, "one call": 2,
    "trial close": 2, "closing": 2,
    "upselling": 2, "cross selling": 2, "cross-selling": 2,
    "spin selling": 2, "spin": 2,
    "sandler": 2, "nepq": 2, "aida": 2, "bant": 2,
}

# Rendimiento / métricas
PERF_HINTS = {
    "kpi": 2, "objetivos": 2, "cuotas": 2,
    "superar metas": 2, "cumplimiento de objetivos": 2,
    "top ventas": 2, "mejor vendedor": 2, "ranking": 2,
    "%": 1, "€": 1, "eur": 1, "euros": 1,
}

# B2C (prioridad)
B2C_HINTS = {
    "b2c": 3, "venta a particulares": 3, "venta retail": 3,
    "venta presencial": 3, "venta directa": 3, "venta puerta fría": 3, "puerta fría": 3,
    "venta domiciliaria": 2, "tienda": 2, "ventas externas": 2,
    "venta telefónica": 1, "venta telefonica": 1,  # telefónica suma poco; “teleoperador” penaliza abajo
}

# B2B (depriorizar)
B2B_PENALTY = {"b2b": -1, "empresas": -1, "venta a empresas": -1}

# Roles no‑closer a penalizar
NON_CLOSER = {
    "teleoperador": -3, "teleoperadora": -3,
    "call center": -3,
    "atención al cliente": -2, "atencion al cliente": -2,
}

# Patrones de duración
RE_YEARS = re.compile(r"(\d+)\s*años?", re.IGNORECASE)
RE_MONTHS = re.compile(r"(\d+)\s*meses?", re.IGNORECASE)
RE_DUR_PARENS = re.compile(r"\(([^)]*años?[^)]*|[^)]*meses?)\)")

# Cabeceras/indicadores claros de inicio de candidato (InfoJobs)
HEADER_HINTS = ["datos del candidato", "ajuste nota", "% ajuste nota", "nota: 40/40"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Palabras que delatan oficio/sección (para NO confundir con nombre)
JOB_HINTS = [
    "teleoperador", "teleoperadora", "comercial", "administr", "limpiador", "limpiadora",
    "camarer", "dependient", "director", "directora", "encargad", "representante",
    "asesor", "asesora", "agente", "grabador", "operari", "auxiliar", "manager",
    "tecnico", "técnico", "tecnica", "técnica"
]
SECTION_HINTS = {"experiencia", "estudios", "idiomas", "conocimientos", "otros", "perfil", "resumen"}

CONNECTORS = {"de", "del", "la", "las", "los", "y", "da", "do", "dos"}

# ==================
# PDF → multi‑CV
# ==================

def _looks_like_name(line: str) -> bool:
    """
    Debe parecer nombre real:
      - 2 a 5 palabras
      - sin dígitos ni '@'
      - no contiene oficios ni secciones
    """
    if not line: return False
    l = line.strip()
    if "@" in l or any(ch.isdigit() for ch in l):
        return False
    w = [t for t in re.split(r"\s+", l) if t]
    if len(w) < 2 or len(w) > 5:
        return False
    lw = l.lower()
    if any(h in lw for h in JOB_HINTS):
        return False
    if any(h in lw for h in SECTION_HINTS):
        return False
    # todas las "palabras" deben ser alfabéticas o conectores
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
            # Title-case manteniendo tildes
            norm.append(base[:1].upper() + base[1:].lower())
    return " ".join(norm)

def _extract_name_from_page(text: str) -> str | None:
    # Busco en las ~10 primeras líneas
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    head = lines[:10]
    for ln in head:
        if _looks_like_name(ln):
            return _normalize_name(ln)
    return None

def _is_header_page(text: str) -> tuple[bool, str | None]:
    """
    Es cabecera solo si:
      - contiene marcadores InfoJobs ("datos del candidato" o "ajuste nota")
        O contiene un email (muy común en cabecera)
      Y
      - extraigo un nombre válido en las primeras líneas
    """
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
    Devuelve lista de dicts: [{"name":..., "text":..., "pages":[i,...]}]
    Agrupa páginas hasta detectar NUEVA cabecera con nombre válido.
    """
    out = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        current = {"name": None, "text": "", "pages": []}
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            is_header, name = _is_header_page(txt)
            if is_header:
                # Si ya hay contenido previo, cerramos candidato anterior
                if current["text"].strip():
                    out.append(current)
                    current = {"name": None, "text": "", "pages": []}
                current["name"] = name
            # Añadimos siempre el texto a current
            current["text"] += (txt + "\n")
            current["pages"].append(i)
        # último bloque
        if current["text"].strip():
            out.append(current)

    # Si algún bloque quedó sin nombre, intento sacarlo de sus primeras líneas
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
            "_texto": c["text"][:8000],  # preview razonable
            "_pdf_paginas": ",".join(str(p+1) for p in c["pages"]),
            "_fuente_pdf": uploaded_file.name,
        })
    return pd.DataFrame(rows)

# ===========================
# Scoring: estabilidad/inglés
# ===========================

def estimate_stability(texto: str):
    """
    (meses_totales, puestos_estables>=12m, muy_cortos<3m)
    """
    t = (texto or "").lower()
    meses_totales = 0
    estables = 0
    muy_cortos = 0

    for m in RE_YEARS.finditer(t):
        meses_totales += int(m.group(1)) * 12
    for m in RE_MONTHS.finditer(t):
        meses_totales += int(m.group(1))

    # bloques estilo "(1 año y 5 meses)"
    for blk in RE_DUR_PARENS.findall(t):
        y = RE_YEARS.search(blk)
        m = RE_MONTHS.search(blk)
        total_blk = (int(y.group(1))*12 if y else 0) + (int(m.group(1)) if m else 0)
        if total_blk >= 12:
            estables += 1
        elif 0 < total_blk < 3:
            muy_cortos += 1

    # heurísticos adicionales
    if re.search(r"(\b1\s*año\b|\b12\s*meses\b)", t):
        estables += 1
    if re.search(r"\b(1|2)\s*mes(es)?\b", t):
        muy_cortos += 1

    return meses_totales, estables, muy_cortos

def english_score(texto: str):
    """
    Puntúa nivel de inglés detectado (máx ~3).
    C2/Nativo:+3 | C1:+2 | B2:+1.5 | B1/intermedio:+1 | avanzado:+1.5 | básico/A2/A1:+0
    Si solo menciona 'inglés' sin nivel: +0.5
    """
    t = (texto or "").lower()
    if "ingl" not in t and "english" not in t:
        return 0.0, ""
    hits = []
    score = 0.0
    if re.search(r"\b(c-?2|nativo)\b", t): score = max(score, 3.0); hits.append("C2/Nativo")
    if re.search(r"\bc1\b", t): score = max(score, 2.0); hits.append("C1")
    if re.search(r"\bb2\b", t): score = max(score, 1.5); hits.append("B2")
    if re.search(r"\bb1\b|\bintermedio\b", t): score = max(score, 1.0); hits.append("B1/Intermedio")
    if re.search(r"\bavanzad", t): score = max(score, 1.5); hits.append("Avanzado")
    if re.search(r"\bbas(ic|ico)\b|\ba2\b|\ba1\b", t): hits.append("Básico/A1-A2")
    if score == 0.0:  # solo menciona inglés
        score = 0.5; hits.append("Inglés (sin nivel)")
    return score, ", ".join(sorted(set(hits)))

# ==========================
# Scoring general (B2C)
# ==========================

def score_closer_b2c(texto: str):
    """
    Suma por CLOSER (cierre/negociación/objeciones/técnicas) + rendimiento (KPIs)
    + señales B2C. Penaliza B2B y roles no-closer. Añade inglés y estabilidad.
    """
    t = (texto or "").lower()
    total = 0
    tags = []

    for k, w in CLOSER_STRONG.items():
        if k in t: total += w; tags.append(k)
    for k, w in PERF_HINTS.items():
        if k in t: total += w; tags.append(k)
    for k, w in B2C_HINTS.items():
        if k in t: total += w; tags.append(k)
    for k, w in B2B_PENALTY.items():
        if k in t: total += w; tags.append(f"-{k}")
    for k, w in NON_CLOSER.items():
        if k in t: total += w; tags.append(f"-{k}")

    eng_pts, eng_hits = english_score(t)
    total += eng_pts
    if eng_hits: tags.append(f"inglés:{eng_hits}")

    meses_totales, estables, muy_cortos = estimate_stability(t)
    total += estables * 2
    total -= muy_cortos * 2

    return {
        "score_total": total,
        "score_ingles": eng_pts,
        "meses_totales": meses_totales,
        "puestos_estables": estables,
        "puestos_muy_cortos": muy_cortos,
        "tags": ", ".join(sorted(set(tags))) if tags else "",
    }

# ==========================
# UI: carga y procesamiento
# ==========================

with st.sidebar:
    st.header("📂 Subida de PDF(s)")
    files = st.file_uploader(
        "Sube PDF(s) de InfoJobs (un PDF puede contener varios CVs)",
        type=["pdf"],
        accept_multiple_files=True
    )
    st.caption("Detecta múltiples CV por PDF usando cabeceras InfoJobs + nombre válido.")

if not files:
    st.info("Sube al menos un PDF para empezar.")
    st.stop()

bloques = []
for f in files:
    try:
        df_pdf = pdf_multi_to_df(f)      # separa pretendientes (1 fila por candidato real)
        bloques.append(df_pdf)
    except Exception as e:
        st.error(f"Error procesando {f.name}: {e}")

if not bloques:
    st.error("No se han podido procesar PDFs válidos.")
    st.stop()

raw_df = pd.concat(bloques, ignore_index=True, sort=False)

# Scoring por candidato
metrics = raw_df["_texto"].apply(score_closer_b2c)
raw_df["Puntuación"]                = [m["score_total"] for m in metrics]
raw_df["score_ingles"]              = [m["score_ingles"] for m in metrics]
raw_df["meses_totales"]             = [m["meses_totales"] for m in metrics]
raw_df["puestos_estables(>=12m)"]   = [m["puestos_estables"] for m in metrics]
raw_df["puestos_muy_cortos(<3m)"]   = [m["puestos_muy_cortos"] for m in metrics]
raw_df["matches"]                   = [m["tags"] for m in metrics]

st.success(f"✅ {len(raw_df)} candidatos detectados y puntuados")

# ============
# Controles UI
# ============
min_s = int(raw_df["Puntuación"].min()) if "Puntuación" in raw_df else 0
max_s = int(raw_df["Puntuación"].max()) if "Puntuación" in raw_df else 0
def_val = min(min_s + 5, max_s) if max_s > min_s else min_s

with st.sidebar:
    umbral = st.slider(
        "Puntuación mínima",
        min_value=min_s,
        max_value=max_s if max_s > min_s else min_s+1,
        value=def_val
    )
    ordenar_por = st.selectbox(
        "Ordenar por",
        options=["Puntuación", "score_ingles", "meses_totales", "puestos_estables(>=12m)",
                 "puestos_muy_cortos(<3m)", "nombre", "_fuente_pdf", "_pdf_paginas"],
        index=0
    )
    asc = st.checkbox("Ascendente", value=False)
    top_n = st.number_input("Ver primeros N", min_value=50, max_value=5000, value=300, step=50)

filtrado = raw_df[raw_df["Puntuación"] >= umbral].sort_values(by=ordenar_por, ascending=asc)

st.subheader(f"📋 Candidatos (puntuación ≥ {umbral})")
cols_prefer = ["nombre","Puntuación","score_ingles","matches","meses_totales",
               "puestos_estables(>=12m)","puestos_muy_cortos(<3m)","_pdf_paginas","_fuente_pdf"]
cols_show = [c for c in cols_prefer if c in filtrado.columns] + [c for c in filtrado.columns if c not in cols_prefer]
st.dataframe(filtrado[cols_show].head(int(top_n)), use_container_width=True)

# ==========
# Exportar
# ==========
csv_out = filtrado.to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Descargar CSV", csv_out, "candidatos_filtrados.csv", "text/csv")

st.caption("B2C + CLOSERS: cierre/negociación/objeciones/técnicas/KPIs + inglés; penaliza teleoperación y rotación corta. Split por cabecera InfoJobs + nombre válido.")
