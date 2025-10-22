# cv.py — Cribado de Candidatos (CSV o PDF) en Streamlit
# - Sube varios archivos (mezclados CSV/PDF)
# - Extrae texto de PDFs (fichas) y calcula una PUNTUACIÓN DE IDONEIDAD
# - Señales: venta telefónica/telemarketing, fidelización, cierres, estabilidad laboral
# - Penaliza rotación (empleos muy cortos), premia puestos ≥12 meses
# - Ordena/filtra por puntuación y permite descargar CSV

import io, os, re, csv
import chardet, pdfplumber, pandas as pd, streamlit as st

st.set_page_config(page_title="Cribado de Candidatos", layout="wide")
st.title("🎯 Cribado de Candidatos (CSV o PDF)")

# -----------------------------
# Utilidades CSV
# -----------------------------
def detectar_encoding_y_delimitador(file_bytes: bytes):
    enc = chardet.detect(file_bytes or b"").get("encoding", "utf-8")
    sample = file_bytes[:15000]
    text = sample.decode(enc, errors="ignore")
    if text.count(";") > text.count(",") and text.count(";") > text.count("\t"):
        delim = ";"
    elif text.count("\t") > text.count(","):
        delim = "\t"
    else:
        delim = ","
    return enc, delim

def leer_csv(uploaded_file):
    raw = uploaded_file.read()
    enc, delim = detectar_encoding_y_delimitador(raw)
    df = pd.read_csv(io.StringIO(raw.decode(enc, errors="replace")), sep=delim)
    df["_fuente_archivo"] = uploaded_file.name
    return df

# -----------------------------
# Utilidades PDF → texto/filas
# -----------------------------
def pdf_a_texto(uploaded_file) -> str:
    with pdfplumber.open(uploaded_file) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)

_nombre_regex = re.compile(r"^[A-ZÁÉÍÓÚÑ][^\n\r]+", re.MULTILINE)
_duracion_parentesis = re.compile(r"\(([^\)]*?años?[^\)]*?|[^\)]*?meses?)\)")
_duracion_aym = re.compile(r"(?:(\d+)\s*años?)?(?:\s*y\s*)?(\d+)\s*meses?", re.IGNORECASE)
_duracion_meses = re.compile(r"(\d+)\s*meses?", re.IGNORECASE)

PALABRAS_FUERZA = [
    "telemarketing", "venta telefónica", "venta telefonica",
    "fidelización", "fidelizacion", "captación", "captacion",
    "cierre de ventas", "orientación a resultados", "orientacion a resultados",
    "call center", "recuperación de clientes", "retención",
    "asesoría energética", "atención telefónica",
]

def extraer_nombre(texto: str) -> str:
    m = _nombre_regex.search(texto.strip())
    if m:
        nombre = m.group(0).strip()
        if "@" not in nombre and not any(x.isdigit() for x in nombre):
            return nombre
        return nombre
    return "(sin nombre)"

def estimar_meses_trabajados(texto: str):
    """Devuelve (meses_totales, puestos_estables(>=12m), puestos_muy_cortos(<3m))."""
    meses_totales = estables = muy_cortos = 0
    for m in _duracion_parentesis.finditer(texto):
        frag = m.group(1)
        mm = _duracion_aym.search(frag)
        if mm:
            años = int(mm.group(1) or 0)
            meses = int(mm.group(2) or 0)
            tot = años * 12 + meses
        else:
            mm2 = _duracion_meses.search(frag)
            tot = int(mm2.group(1)) if mm2 else 0
        meses_totales += tot
        if tot >= 12: estables += 1
        if 0 < tot < 3: muy_cortos += 1
    return meses_totales, estables, muy_cortos

def score_desde_texto(texto: str) -> dict:
    t = texto.lower()
    score, hits = 0, []
    for p in PALABRAS_FUERZA:
        if p in t:
            score += 3
            hits.append(p)
    meses_totales, estables, muy_cortos = estimar_meses_trabajados(texto)
    score += estables * 2        # premiar empleos largos
    score -= muy_cortos * 2      # penalizar cambios breves
    if "fidelización" in t or "fidelizacion" in t: score += 2
    if "orientación a resultados" in t or "orientacion a resultados" in t: score += 2
    return {
        "score": score,
        "hits": ", ".join(sorted(set(hits))) or "",
        "meses_totales": meses_totales,
        "puestos_estables": estables,
        "puestos_muy_cortos": muy_cortos,
    }

def pdf_a_fila(uploaded_file) -> pd.DataFrame:
    texto = pdf_a_texto(uploaded_file)
    nombre = extraer_nombre(texto)
    m = score_desde_texto(texto)
    return pd.DataFrame([{
        "nombre": nombre,
        "Puntuación": m["score"],
        "matches": m["hits"],
        "meses_totales": m["meses_totales"],
        "puestos_estables(>=12m)": m["puestos_estables"],
        "puestos_muy_cortos(<3m)": m["puestos_muy_cortos"],
        "_fuente_archivo": uploaded_file.name,
        "_texto": texto[:2000],
    }])

# -----------------------------
# Lector genérico (CSV o PDF)
# -----------------------------
st.sidebar.header("📂 Subida de archivos")
archivos = st.sidebar.file_uploader("Sube CSV y/o PDF de candidatos", type=["csv", "pdf"], accept_multiple_files=True)
if not archivos:
    st.info("Sube tus archivos para comenzar el cribado.")
    st.stop()

filas = []
for f in archivos:
    ext = os.path.splitext(f.name)[1].lower()
    try:
        if ext == ".csv":
            df = leer_csv(f)
            if "_texto" not in df.columns:
                df_text = df.astype(str).apply(lambda r: " ".join(r.values), axis=1)
            else:
                df_text = df["_texto"].astype(str)
            scores = df_text.apply(score_desde_texto)
            df["Puntuación"] = [s["score"] for s in scores]
            df["matches"] = [s["hits"] for s in scores]
            df["meses_totales"] = [s["meses_totales"] for s in scores]
            df["puestos_estables(>=12m)"] = [s["puestos_estables"] for s in scores]
            df["puestos_muy_cortos(<3m)"] = [s["puestos_muy_cortos"] for s in scores]
            filas.append(df)
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

# Controles
min_score = int(resultado["Puntuación"].min()) if "Puntuación" in resultado else 0
max_score = int(resultado["Puntuación"].max()) if "Puntuación" in resultado else 0
valor_def = min(min_score + 5, max_score) if max_score > min_score else min_score
umbral = st.sidebar.slider("Puntuación mínima", min_value=min_score, max_value=max_score if max_score>min_score else min_score+1, value=valor_def)

orden = st.sidebar.selectbox("Ordenar por", options=["Puntuación", "meses_totales", "puestos_estables(>=12m)", "puestos_muy_cortos(<3m)", "nombre"], index=0)
asc = st.sidebar.checkbox("Ascendente", value=False)

filtrado = resultado[resultado["Puntuación"] >= umbral].sort_values(by=orden, ascending=asc)

st.subheader(f"📋 Candidatos (puntuación ≥ {umbral})")
cols_show = [c for c in ["nombre","Puntuación","matches","meses_totales","puestos_estables(>=12m)","puestos_muy_cortos(<3m)","_fuente_archivo"] if c in filtrado.columns]
st.dataframe(filtrado[cols_show].head(200), use_container_width=True)

# Descarga
csv_out = filtrado.to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Descargar CSV", csv_out, "candidatos_filtrados.csv", "text/csv")

st.caption("La puntuación prioriza venta telefónica, telemarketing, fidelización y estabilidad laboral; penaliza rotación corta.")
