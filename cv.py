# app.py — Cribado de CSV en entorno web (Streamlit)
# -------------------------------------------------
# Características:
# - Subir múltiples CSV a mano (drag & drop)
# - Autodetección de encoding y separador
# - Unión de todos los CSV en una sola tabla
# - Filtros automáticos por tipo de dato (numérico, fecha, texto, categorías)
# - Búsqueda por texto libre (multi‑columnas)
# - Deduplicado opcional por columna(s)
# - Exportar resultado a CSV
# - Guardar/cargar presets de filtros en la sesión
#
# Uso local:
#   pip install streamlit pandas chardet python-dateutil
#   streamlit run app.py
#
# Despliegue rápido:
# - Streamlit Community Cloud (subir repo con este archivo)
# - Docker (ver ejemplo más abajo en comentarios)

import io
import csv
import json
import chardet
import pandas as pd
import streamlit as st
from dateutil import parser as dateparser

st.set_page_config(page_title="Cribado CSV", layout="wide")
st.title("📎 Cribado de CSV en lote")

# ------------------------
# Helpers
# ------------------------

def detect_encoding_and_delimiter(file_bytes: bytes):
    """Devuelve (encoding, delimiter). Intenta detectar ambos de forma robusta."""
    # Detectar encoding
    enc_guess = chardet.detect(file_bytes or b"")
    encoding = enc_guess.get("encoding") or "utf-8"

    # Delimiter: probar con csv.Sniffer sobre una muestra
    sample = file_bytes[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample.decode(encoding, errors="ignore"), delimiters=",;\t|:")
        delimiter = dialect.delimiter
    except Exception:
        # fallback heurístico
        text = sample.decode(encoding, errors="ignore")
        if text.count(";") > text.count(",") and text.count(";") > text.count("\t"):
            delimiter = ";"
        elif text.count("\t") > text.count(","):
            delimiter = "\t"
        else:
            delimiter = ","
    return encoding, delimiter


def read_csv_file(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.read()
    enc, delim = detect_encoding_and_delimiter(raw)
    # Volver a la posición para pandas
    buffer = io.StringIO(raw.decode(enc, errors="replace"))
    try:
        df = pd.read_csv(buffer, sep=delim, engine="python")
    except Exception:
        buffer.seek(0)
        df = pd.read_csv(buffer, sep=delim, engine="python", on_bad_lines="skip")
    return df


def to_datetime_safe(series: pd.Series):
    try:
        return pd.to_datetime(series, errors="coerce", infer_datetime_format=True)
    except Exception:
        return pd.to_datetime(series.astype(str), errors="coerce")


def suggest_dtype(series: pd.Series):
    # Intenta detectar fecha, numérico o texto/categórico
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    # fecha candidata si >60% parseable
    dt = to_datetime_safe(series)
    if dt.notna().mean() > 0.6:
        return "date"
    # categórico si pocos valores únicos
    if series.nunique(dropna=True) <= max(20, int(0.05 * len(series))):
        return "category"
    return "text"


def text_search(df: pd.DataFrame, query: str, columns=None):
    if not query:
        return df
    columns = columns or df.columns
    mask = pd.Series(False, index=df.index)
    for col in columns:
        s = df[col].astype(str).str.contains(query, case=False, na=False)
        mask = mask | s
    return df[mask]


# ------------------------
# Sidebar: Carga y opciones
# ------------------------
with st.sidebar:
    st.header("Carga de archivos")
    files = st.file_uploader("Sube uno o varios CSV", type=["csv"], accept_multiple_files=True)

    st.divider()
    st.subheader("Opciones")
    keep_all_cols = st.checkbox("Mantener nombres de columnas tal cual", value=True)
    strip_cols = st.checkbox("Limpiar espacios en nombres de columnas", value=True)
    lowercase_cols = st.checkbox("Pasar nombres de columnas a minúsculas", value=True)

    st.subheader("Deduplicado")
    dedup = st.checkbox("Eliminar duplicados")
    dedup_cols = st.text_input("Columnas para deduplicar (separadas por coma)")
    keep_first = st.selectbox("Si hay duplicados, conservar", ["primero", "último"])

    st.subheader("Presets de filtros")
    saved = st.session_state.get("presets", {})
    preset_name = st.text_input("Nombre del preset")
    if st.button("Guardar preset"):
        st.session_state.setdefault("presets", {})[preset_name] = st.session_state.get("filters", {})
        st.success(f"Preset '{preset_name}' guardado")
    if saved:
        chosen = st.selectbox("Cargar preset", ["—"] + list(saved.keys()))
        if chosen != "—" and st.button("Aplicar preset"):
            st.session_state["filters"] = saved[chosen]
            st.success(f"Preset '{chosen}' aplicado")


# ------------------------
# Carga y unión de CSV
# ------------------------
if not files:
    st.info("Sube tus CSV en la barra lateral para empezar.")
    st.stop()

frames = []
for f in files:
    df = read_csv_file(f)
    if strip_cols:
        df.columns = [c.strip() for c in df.columns]
    if lowercase_cols:
        df.columns = [c.lower() for c in df.columns]
    frames.append(df)

if not frames:
    st.error("No se pudieron leer archivos.")
    st.stop()

# Unir por filas, alineando columnas
data = pd.concat(frames, ignore_index=True, sort=False)

if not keep_all_cols:
    # Opción para recortar columnas inexistentes en todos (no recomendado por defecto)
    common = set(frames[0].columns)
    for df in frames[1:]:
        common &= set(df.columns)
    data = data[list(common)]

st.success(f"Archivos cargados: {len(files)} | Filas totales: {len(data):,} | Columnas: {len(data.columns)}")

# Deduplicado
if dedup:
    subset = [c.strip() for c in dedup_cols.split(",") if c.strip()] or None
    keep = "first" if keep_first == "primero" else "last"
    before = len(data)
    data = data.drop_duplicates(subset=subset, keep=keep)
    st.caption(f"🔁 Deduplicado: {before - len(data)} filas eliminadas")

# Preview
with st.expander("Ver muestra de datos", expanded=True):
    st.dataframe(data.head(100), use_container_width=True)

# ------------------------
# Construcción dinámica de filtros
# ------------------------
st.subheader("Filtros")
filters_state = st.session_state.setdefault("filters", {})

col1, col2 = st.columns([2, 1])
with col1:
    q = st.text_input("Búsqueda rápida (contiene, todas las columnas)")
with col2:
    limit_preview = st.number_input("Ver primeras N filas", min_value=10, max_value=100000, value=500, step=10)

# Meta de tipos
inferred_types = {c: suggest_dtype(data[c]) for c in data.columns}

with st.expander("Ajustar filtros por columna", expanded=True):
    for c in data.columns:
        t = inferred_types[c]
        key = f"flt::{c}"
        cur = filters_state.get(key)

        if t == "number":
            min_v = float(pd.to_numeric(data[c], errors="coerce").min())
            max_v = float(pd.to_numeric(data[c], errors="coerce").max())
            val = st.slider(f"{c} (entre)", min_value=min_v, max_value=max_v, value=cur or (min_v, max_v))
            filters_state[key] = val
        elif t == "date":
            s = to_datetime_safe(data[c])
            min_d, max_d = s.min(), s.max()
            if pd.isna(min_d) or pd.isna(max_d):
                continue
            val = st.date_input(f"{c} (rango)", value=cur or (min_d.date(), max_d.date()))
            filters_state[key] = val
        elif t == "category":
            opts = sorted([x for x in data[c].dropna().astype(str).unique()])
            val = st.multiselect(f"{c} (categorías)", options=opts, default=cur or [])
            filters_state[key] = val
        else:  # text
            val = st.text_input(f"{c} contiene (texto)", value=cur or "")
            filters_state[key] = val

# Aplicar filtros
filtered = data.copy()

# Búsqueda libre
filtered = text_search(filtered, q)

# Filtros por columna
for c in data.columns:
    t = inferred_types[c]
    key = f"flt::{c}"
    val = filters_state.get(key)
    if val in (None, ""):
        continue

    if t == "number":
        s = pd.to_numeric(filtered[c], errors="coerce")
        filtered = filtered[(s >= val[0]) & (s <= val[1])]
    elif t == "date":
        s = to_datetime_safe(filtered[c])
        start, end = pd.to_datetime(val[0]), pd.to_datetime(val[1])
        filtered = filtered[(s >= start) & (s <= end)]
    elif t == "category":
        if val:
            filtered = filtered[filtered[c].astype(str).isin([str(x) for x in val])]
    else:  # text
        if val:
            filtered = filtered[filtered[c].astype(str).str.contains(val, case=False, na=False)]

st.markdown(f"**Resultado:** {len(filtered):,} filas")
st.dataframe(filtered.head(int(limit_preview)), use_container_width=True)

# ------------------------
# Exportar
# ------------------------
@st.cache_data
def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")

csv_bytes = to_csv_bytes(filtered)
st.download_button(
    label="⬇️ Descargar CSV filtrado",
    data=csv_bytes,
    file_name="cribado_resultado.csv",
    mime="text/csv",
)

# ------------------------
# Notas de despliegue (Docker)
# ------------------------
# Ejemplo rápido de Dockerfile:
#   FROM python:3.11-slim
#   WORKDIR /app
#   COPY app.py /app/app.py
#   RUN pip install --no-cache-dir streamlit pandas chardet python-dateutil
#   EXPOSE 8501
#   CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
# Build & run:
#   docker build -t cribado-csv .
#   docker run -p 8501:8501 cribado-csv
