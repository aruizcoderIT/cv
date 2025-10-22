# cv.py — Cribado de Candidatos (CSV o PDF) orientado a CLOSERS
# --------------------------------------------------------------
# - Sube varios archivos (mezclados CSV/PDF)
# - Extrae texto de PDFs y calcula una PUNTUACIÓN DE IDONEIDAD
# - Pesa fuerte: cierre, negociación, objeciones, KPIs, venta consultiva, pipeline, B2B, CRM
# - Penaliza: teleoperación/call center, rotación corta (<3m)
# - Bonifica estabilidad razonable (≥12m) sin sobrepremiar estancamiento
# - Ordena/filtra por puntuación y descarga CSV con el resultado

import io, os, re
import chardet
import pdfplumber
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cribado de Candidatos", layout="wide")
st.title("🎯 Cribador de CLOSERS (CSV o PDF)")

# =========================
# Config de scoring CLOSER
# =========================

CLOSER_KEYWORDS = {
    # Cierre / negociación (fuerte)
    "cierre de ventas": 4,
    "cerrador": 4,
    "técnicas de cierre": 4,
    "tecnicas de cierre": 4,
    "negociación": 4,
    "negociacion": 4,
    "objeciones": 4,
    "venta consultiva": 4,
    "venta de alto valor": 4,
    "venta b2b": 4,
    "pipeline": 4,
    "pipeline comercial": 4,
    "prospección": 3,
    "prospectacion": 3,

    # Rendimiento / métricas / dirección comercial
    "kpi": 3,
    "objetivos": 3,
    "cuotas": 3,
    "superar metas": 3,
    "cumplimiento de objetivos": 3,
    "top ventas": 3,
    "mejor vendedor": 3,
    "ranking": 3,
    "crm": 3,
    "venta directa": 3,
    "venta cruzada": 3,
    "venta recurrente": 3,

    # Fidelización & retención (útiles, pero menos que cierre puro)
    "fidelización": 2,
    "fidelizacion": 2,
    "retención": 2,
    "retencion": 2,
}

# Señales a penalizar (perfil no-closer)
NON_CLOSER_HINTS = {
    "teleoperador": -3,
    "teleoperadora": -3,
    "telemarketing": -3,     # si en tu negocio hay buen telemarketing, bájalo a -1
    "call center": -3,
    "atención al cliente": -2,
    "atencion al cliente": -2,
}

# Campos típicos de InfoJobs (pueden no estar)
INFOJOBS_TEXT_FIELDS = [
    "skills", "competencias", "conocimientos", "habilidades",
    "experiencia", "experiences", "funciones", "resumen", "descripcion",
    "puestos", "tareas", "logros", "otros_datos",
]

# Patrones de duración estilo InfoJobs
RE_YEARS = re.compile(r"(\d+)\s*años?", re.IGNORECASE)
RE_MONTHS = re.compile(r"(\d+)\s*meses?", re.IGNORECASE)
RE_DUR_PARENS = re.compile(r"\(([^)]*años?[^)]*|[^)]*meses?)\)")  # capturar “(1 año y 5 meses)”, etc.

# ============
# CSV helpers
# ============

def detectar_encoding_y_delimitador(file_bytes: bytes):
    enc = chardet.detect(file_bytes or b"").get("encoding", "utf-8")
    sample = file_bytes[:20000]
    text = sample.decode(enc, errors="ignore")
    if text.count(";") > text.count(",") and text.count(";") > text.count("\t"):
        delim = ";"
    elif text.count("\t") > text.count(","):
        delim = "\t"
    else:
        delim = ","
    return enc, delim

def leer_csv(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.read()
    enc, delim = detectar_encoding_y_delimitador(raw)
    df = pd.read_csv(io.StringIO(raw.decode(enc, errors="replace")), sep=delim, engine="python")
    df["_fuente_archivo"] = uploaded_file.name
    return df

# ============
# PDF helpers
# ============

def pdf_a_texto(uploaded_file) -> str:
    with pdfplumber.open(uploaded_file) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)

def extraer_nombre(texto: str) -> str:
    # Toma la primera línea “tipo nombre” (heurístico)
    for line in (texto or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "@" in line or any(ch.isdigit() for ch in line):
            continue
        return line
    return "(sin nombre)"

# =====================
# Scoring & agregación
# =====================

def _meses_estimados(texto: str) -> tuple[int, int, int]:
    """
    Devuelve (meses_totales, puestos_estables(>=12m), puestos_muy_cortos(<3m))
    a partir del texto libre. Heurístico robusto para InfoJobs.
    """
    t = (texto or "").lower()
    meses_totales = 0
    estables = 0
    muy_cortos = 0

    # Suma todas las duraciones que encuentre (años y meses)
    for m in RE_YEARS.finditer(t):
        meses_totales += int(m.group(1)) * 12
    for m in RE_MONTHS.finditer(t):
        meses_totales += int(m.group(1))

    # Estabilidad / muy cortos (heurístico básico)
    if re.search(r"(\b1\s*año\b|\b12\s*meses\b)", t):
        estables += 1
    if re.search(r"\b(1|2)\s*mes(es)?\b", t):
        muy_cortos += 1

    # Plus: si encontramos varios paréntesis con duraciones, contamos estables/cortos por bloque
    for blk in RE_DUR_PARENS.findall(t):
        y = RE_YEARS.search(blk)
        m = RE_MONTHS.search(blk)
        total_blk = (int(y.group(1))*12 if y else 0) + (int(m.group(1)) if m else 0)
        if total_blk >= 12:
            estables += 1
        elif 0 < total_blk < 3:
            muy_cortos += 1

    return meses_totales, estables, muy_cortos

def _score_texto_closer(txt: str) -> dict:
    """
    Scoring orientado a closers:
    +4/+3 por señales de cierre/negociación/KPI
    penaliza roles de teleoperación
    bonifica estabilidad >=12m y penaliza <3m
    pequeño bonus por números (%/€/cifras) como indicio de KPIs
    """
    t = (txt or "").lower()
    score = 0
    hits = []

    # keywords pro-closer
    for k, w in CLOSER_KEYWORDS.items():
        if k in t:
            score += w
            hits.append(k)

    # penalizaciones
    for k, w in NON_CLOSER_HINTS.items():
        if k in t:
            score += w
            hits.append(f"-{k}")

    # estabilidad/rotación
    meses_totales, estables, muy_cortos = _meses_estimados(t)
    score += estables * 2
    score -= muy_cortos * 2

    # señales numéricas (posibles KPIs: %, €, cifras)
    if re.search(r"\d+\s*%|€|eur|euros|\b\d{2,}\b", t):
        score += 1

    return {
        "score": score,
        "hits": ", ".join(sorted(set(hits))) if hits else "",
        "meses_totales": meses_totales,
        "puestos_estables": estables,
        "puestos_muy_cortos": muy_cortos,
    }

def texto_candidato(row: pd.Series) -> str:
    """
    Construye un texto robusto a partir de las columnas disponibles (InfoJobs es irregular).
    Si no hay columnas ricas, concatena toda la fila.
    """
    trozos = []
    for col in INFOJOBS_TEXT_FIELDS:
        if col in row and pd.notna(row[col]):
            trozos.append(str(row[col]))
    if not trozos:
        trozos = [" ".join([str(v) for v in row.astype(str).values])]
    return " \n ".join(trozos)

def calcular_puntuacion_df_textos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica _score_texto_closer sobre un DataFrame que puede venir de CSV o construirse desde PDF.
    Si existe columna '_texto', la usa; si no, compone texto desde columnas típicas de InfoJobs.
    """
    df = df.copy()
    if "_texto" in df.columns:
        textos = df["_texto"].astype(str)
    else:
        textos = df.apply(texto_candidato, axis=1)

    metrics = textos.apply(_score_texto_closer)
    df["Puntuación"] = [m["score"] for m in metrics]
    df["matches"] = [m["hits"] for m in metrics]
    df["meses_totales"] = [m["meses_totales"] for m in metrics]
    df["puestos_estables(>=12m)"] = [m["puestos_estables"] for m in metrics]
    df["puestos_muy_cortos(<3m)"] = [m["puestos_muy_cortos"] for m in metrics]
    return df

def pdf_a_fila(uploaded_file) -> pd.DataFrame:
    texto = pdf_a_texto(uploaded_file)
    nombre = extraer_nombre(texto)
    metrics = _score_texto_closer(texto)
    return pd.DataFrame([{
        "nombre": nombre,
        "_texto": texto[:4000],  # preview limitado
        "Puntuación": metrics["score"],
        "matches": metrics["hits"],
        "meses_totales": metrics["meses_totales"],
        "puestos_estables(>=12m)": metrics["puestos_estables"],
        "puestos_muy_cortos(<3m)": metrics["puestos_muy_cortos"],
        "_fuente_archivo": uploaded_file.name,
    }])

# ==========================
# UI: carga y procesamiento
# ==========================

with st.sidebar:
    st.header("📂 Subida de archivos")
    files = st.file_uploader("Sube CSV y/o PDF de candidatos", type=["csv", "pdf"], accept_multiple_files=True)
    st.caption("Puedes mezclar CSV y PDF en la misma carga.")

if not files:
    st.info("Sube tus archivos para comenzar el cribado.")
    st.stop()

filas = []
for f in files:
    ext = os.path.splitext(f.name)[1].lower()
    try:
        if ext == ".csv":
            df_csv = leer_csv(f)
            df_scored = calcular_puntuacion_df_textos(df_csv)
            filas.append(df_scored)
        elif ext == ".pdf":
            filas.append(pdf_a_fila(f))
        else:
            st.warning(f"Formato no soportado: {f.name}")
    except Exception as e:
        st.error(f"Error procesando {f.name}: {e}")

if not filas:
    st.error("No se han podido procesar archivos válidos.")
    st.stop()

resultado = pd.concat(filas, ignore_index=True, sort=False)

st.success(f"✅ {len(resultado)} candidatos procesados")

# ============
# Controles UI
# ============
min_s = int(resultado["Puntuación"].min()) if "Puntuación" in resultado else 0
max_s = int(resultado["Puntuación"].max()) if "Puntuación" in resultado else 0
def_val = min(min_s + 5, max_s) if max_s > min_s else min_s

with st.sidebar:
    umbral = st.slider("Puntuación mínima", min_value=min_s, max_value=max_s if max_s > min_s else min_s+1, value=def_val)
    ordenar_por = st.selectbox("Ordenar por", options=[
        "Puntuación", "meses_totales", "puestos_estables(>=12m)", "puestos_muy_cortos(<3m)", "nombre"
    ], index=0)
    asc = st.checkbox("Ascendente", value=False)
    top_n = st.number_input("Ver primeros N", min_value=50, max_value=5000, value=300, step=50)

filtrado = resultado[resultado["Puntuación"] >= umbral].sort_values(by=ordenar_por, ascending=asc)

st.subheader(f"📋 Candidatos (puntuación ≥ {umbral})")
cols_prefer = ["nombre","Puntuación","matches","meses_totales","puestos_estables(>=12m)","puestos_muy_cortos(<3m)","_fuente_archivo"]
cols_show = [c for c in cols_prefer if c in filtrado.columns] + [c for c in filtrado.columns if c not in cols_prefer]
st.dataframe(filtrado[cols_show].head(int(top_n)), use_container_width=True)

# ==========
# Exportar
# ==========
csv_out = filtrado.to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Descargar CSV", csv_out, "candidatos_filtrados.csv", "text/csv")

st.caption("El score prioriza CLOSERS: cierre, negociación, KPIs, venta consultiva, pipeline, B2B/alto valor; penaliza teleoperación y rotación corta.")
