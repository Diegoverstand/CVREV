import streamlit as st
import pandas as pd
from PyPDF2 import PdfReader
from docx import Document
import google.generativeai as genai
import io
import time
import zipfile
import json
import re
import sqlite3
import hashlib
from datetime import datetime
from fpdf import FPDF
import plotly.express as px

# ==============================================================================
# 1. CONFIGURACIÓN INICIAL Y ESTILOS
# ==============================================================================

st.set_page_config(
    page_title="HR Intelligence Suite Pro",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="expanded"
)

# Estilos CSS Nativos (Se adaptan a Tema Claro/Oscuro)
st.markdown("""
    <style>
    .main .block-container { padding-top: 2rem; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; border-radius: 5px; }
    div[data-testid="stExpander"] { border: 1px solid #444; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. DEFINICIÓN DE HERRAMIENTAS Y FUNCIONES CORE
# ==============================================================================

# --- Capa de Datos (SQLite) ---
@st.cache_resource
def init_db():
    conn = sqlite3.connect('cv_master_db.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analisis (
                    file_hash TEXT PRIMARY KEY, timestamp TEXT, lote_nombre TEXT, archivo_nombre TEXT,
                    candidato TEXT, facultad TEXT, cargo TEXT, puntaje REAL, recomendacion TEXT,
                    ajuste TEXT, raw_json TEXT, pdf_blob BLOB
                )''')
    conn.commit()
    return conn

conn = init_db()

def get_file_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()

def db_check_exists(file_hash):
    c = conn.cursor()
    c.execute("SELECT candidato FROM analisis WHERE file_hash = ?", (file_hash,))
    return c.fetchone() is not None

def db_save_record(data_dict, pdf_bytes, file_hash, filename, lote_name):
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json_str = json.dumps(data_dict, ensure_ascii=False)
    c.execute('''INSERT OR REPLACE INTO analisis VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (file_hash, now, lote_name, filename, data_dict.get('nombre'),
               data_dict.get('facultad'), data_dict.get('cargo'), data_dict.get('puntaje_global'),
               data_dict.get('recomendacion'), data_dict.get('ajuste'), json_str, pdf_bytes))
    conn.commit()

def db_load_all():
    return pd.read_sql("SELECT * FROM analisis ORDER BY timestamp DESC", conn)

# --- Motores de Lectura y IA ---
def read_file_safe(file_obj):
    try:
        file_obj.seek(0)
        if file_obj.name.endswith('.pdf'):
            reader = PdfReader(file_obj)
            return "".join([p.extract_text() or "" for p in reader.pages])
        elif file_obj.name.endswith('.docx'):
            doc = Document(file_obj)
            return "\n".join([p.text for p in doc.paragraphs])
        return ""
    except Exception: return ""

def analyze_with_ai(text, role, faculty, api_key):
    if not api_key: return None
    genai.configure(api_key=api_key)
    
    # Lógica de Autodescubrimiento de Modelo
    model_name = 'gemini-1.5-flash' # Default
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in models:
            if 'flash' in m.lower() and ('1.5' in m or '2.0' in m):
                model_name = m; break
    except: pass
    
    prompt = f"""
    Eres un experto en Selección Académica. Evalúa este CV.
    Facultad: {faculty} | Cargo: {role}
    Reglas:
    1. Calcula el puntaje de cada dimensión (0-5).
    2. Calcula el Puntaje Ponderado (Formación: 35%, Experiencia: 30%, Competencias: 20%, Software: 15%).
    3. Suma para el Puntaje Global (0.00-5.00).
    4. Asigna Recomendación: 'AVANZA' (>=3.75), 'REQUIERE ANTECEDENTES' (3.00-3.74), 'NO RECOMENDADO' (<3.00).
    
    Output JSON (Estricto):
    {{
        "nombre": "Nombre Completo",
        "ajuste": "Alto/Medio/Bajo",
        "puntaje_global": 0.00,
        "recomendacion": "ESTADO",
        "conclusion_ejecutiva": "Resumen ejecutivo.",
        "detalle_puntajes": {{
            "formacion": {{ "nota": 0, "ponderado": 0.00 }},
            "experiencia": {{ "nota": 0, "ponderado": 0.00 }},
            "competencias": {{ "nota": 0, "ponderado": 0.00 }},
            "software": {{ "nota": 0, "ponderado": 0.00 }}
        }},
        "analisis_cualitativo": {{
            "brechas": ["..."], "riesgos": ["..."], "fortalezas": ["..."]
        }}
    }}
    CV TEXTO: {text[:30000]}
    """
    
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        raw = response.text
        start, end = raw.find('{'), raw.rfind('}') + 1
        return json.loads(raw[start:end]) if start != -1 else None
    except Exception as e:
        st.error(f"Fallo en IA ({model_name}): {e}")
        return None

# --- Generador de PDF Corporativo ---
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14); self.cell(0, 10, 'INFORME DE AJUSTE CANDIDATO-CARGO', 0, 1, 'C'); self.ln(5)
    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8); self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

def generate_pdf_report(data):
    pdf = PDFReport()
    pdf.add_page()
    def txt(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    
    pdf.set_font('Arial', 'B', 11); pdf.cell(30, 6, "Candidato:", 0, 0); pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, txt(data.get('nombre', '')), 0, 1)
    pdf.set_font('Arial', 'B', 11); pdf.cell(30, 6, "Cargo:", 0, 0); pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, txt(f"{data.get('cargo')} / {data.get('facultad')}"), 0, 1); pdf.ln(8)
    
    pdf.set_font('Arial', 'B', 12); pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("A. CONCLUSIÓN EJECUTIVA"), 1, 1, 'L', True); pdf.ln(2)
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 5, txt(f"Nivel de ajuste: {data.get('ajuste')}. Puntaje: {data.get('puntaje_global', 0.0):.2f}/5.00.\n{data.get('conclusion_ejecutiva')}")); pdf.ln(5)
    
    rec = data.get('recomendacion', '').upper()
    if "NO" in rec: pdf.set_fill_color(255, 200, 200)
    elif "AVANZA" in rec: pdf.set_fill_color(200, 255, 200)
    else: pdf.set_fill_color(255, 255, 200)
    pdf.set_font('Arial', 'B', 12); pdf.cell(0, 10, txt(rec), 1, 1, 'C', True); pdf.ln(8)
    
    pdf.set_font('Arial', 'B', 12); pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("B. TABLA RESUMEN DE CALIFICACIÓN"), 1, 1, 'L', True)
    pdf.set_font('Arial', 'B', 9); pdf.set_fill_color(50, 50, 50); pdf.set_text_color(255)
    pdf.cell(80, 8, "Dimensión", 1, 0, 'L', True); pdf.cell(30, 8, "Ponderación", 1, 0, 'C', True)
    pdf.cell(30, 8, "Puntaje (0-5)", 1, 0, 'C', True); pdf.cell(50, 8, "Puntaje Ponderado", 1, 1, 'C', True)
    pdf.set_text_color(0); pdf.set_font('Arial', '', 9)
    
    det = data.get('detalle_puntajes', {})
    dims = [("Formación", "35%", det.get('formacion', {})), ("Experiencia", "30%", det.get('experiencia', {})),
            ("Competencias", "20%", det.get('competencias', {})), ("Software", "15%", det.get('software', {}))]
    for n, p, v in dims:
        pdf.cell(80, 8, txt(n), 1); pdf.cell(30, 8, p, 1, 0, 'C'); pdf.cell(30, 8, str(v.get('nota', 0)), 1, 0, 'C'); pdf.cell(50, 8, f"{v.get('ponderado', 0):.2f}", 1, 1, 'C')
    
    pdf.set_font('Arial', 'B', 9); pdf.set_fill_color(230,230,230)
    pdf.cell(140, 8, "TOTAL PONDERADO", 1, 0, 'R', True); pdf.cell(50, 8, f"{data.get('puntaje_global', 0.0):.2f} / 5.00", 1, 1, 'C', True); pdf.ln(8)
    
    pdf.set_font('Arial', 'B', 12); pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("C. COMENTARIOS FINALES"), 1, 1, 'L', True); pdf.ln(2)
    pdf.set_font('Arial', '', 10)
    cual = data.get('analisis_cualitativo', {})
    for k, v in cual.items():
        pdf.set_font('Arial', 'B', 10); pdf.cell(0, 6, txt(k.capitalize()), 0, 1)
        pdf.set_font('Arial', '', 10)
        items = v if isinstance(v, list) else [str(v)]
        for i in items: pdf.multi_cell(0, 5, txt(f"- {i}"))
        pdf.ln(2)
        
    return bytes(pdf.output())

# --- Lógica de Procesamiento Centralizada ---
def execute_processing(batches_to_run, api_key, skip_dupes):
    total_files = sum(len(b['files']) for b in batches_to_run)
    est_time = total_files * 5
    st.info(f"⏱️ Analizando {total_files} documentos... Tiempo estimado: ~{est_time // 60} min {est_time % 60} seg.")
    
    progress_bar = st.progress(0, "Iniciando...")
    status_log_placeholder = st.empty()
    live_table_placeholder = st.empty()
    
    processed, skipped, errors = 0, 0, 0
    log_messages = []

    for batch in batches_to_run:
        for file in batch['files']:
            # 1. Hashear y verificar duplicidad
            file.seek(0); file_bytes = file.read(); file_hash = get_file_hash(file_bytes)
            
            if skip_dupes and db_check_exists(file_hash):
                skipped += 1
            else:
                # 2. Leer y Analizar
                text = read_file_safe(file)
                if len(text) > 50:
                    ai_data = analyze_with_ai(text, batch['rol'], batch['fac'], api_key)
                    if ai_data:
                        # 3. Guardar
                        ai_data.update({'facultad': batch['fac'], 'cargo': batch['rol']})
                        pdf_bytes = generate_pdf_report(ai_data)
                        db_save_record(ai_data, pdf_bytes, file_hash, file.name, batch['id'])
                        processed += 1
                    else: errors += 1
                else: errors += 1
            
            # 4. Actualizar UI
            completed = processed + skipped + errors
            progress_bar.progress(completed / total_files, f"Progreso: {completed}/{total_files}")
            if processed > 0: log_messages.append(f"✅ {file.name}")
            elif skipped > 0 and skip_dupes and db_check_exists(file_hash): log_messages.append(f"🔄 {file.name} (Duplicado)")
            else: log_messages.append(f"❌ {file.name} (Error)")
            status_log_placeholder.info("\n".join(log_messages[-5:])) # Últimos 5 logs
            
            # Streaming en vivo a la tabla de la pestaña de Datos
            with live_table_placeholder.container():
                df_live = db_load_all()
                if not df_live.empty:
                    st.dataframe(df_live.head(10)[['timestamp', 'lote_nombre', 'candidato', 'puntaje', 'recomendacion']], use_container_width=True)

    st.success(f"Finalizado. Nuevos: {processed}, Omitidos: {skipped}, Errores: {errors}")
    time.sleep(3); st.rerun()

# ==============================================================================
# 6. INTERFAZ GRÁFICA (UI)
# ==============================================================================

# --- Sidebar ---
with st.sidebar:
    st.title("⚙️ Panel de Control")
    api_key = st.text_input("Google API Key", type="password")
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("Licencia Corporativa Activa")
    
    st.divider()
    skip_dupes = st.checkbox("Omitir duplicados", value=True)
    if st.button("🔴 Borrar Historial"):
        conn.cursor().execute("DELETE FROM analisis"); conn.commit(); st.rerun()

# --- Main ---
st.title("🚀 HR Intelligence Suite")
st.markdown("Sistema integral de análisis curricular asistido por IA.")

# --- Pestañas ---
tab1, tab2, tab3, tab4 = st.tabs(["⚡ Centro de Carga", "📊 Dashboard Ejecutivo", "🗃️ Base de Datos", "📂 Repositorio de Informes"])

# --- TAB 1: Carga ---
with tab1:
    # Definiciones
    FACULTADES = ["Facultad de Ingeniería", "Facultad de Economía y Negocios", "Facultad de Ciencias de la Vida", "Facultad de Educación y Ciencias Sociales"]
    CARGOS = ["Docente", "Investigador", "Gestión Académica"]
    
    col1, col2 = st.columns(2)
    batches_to_run = []

    def render_batch_ui(column, idx):
        with column, st.container(border=True):
            st.subheader(f"📂 Lote #{idx}")
            files = st.file_uploader(f"Archivos Lote {idx}", accept_multiple_files=True, key=f"u{idx}")
            fac = st.selectbox("Facultad", FACULTADES, key=f"f{idx}")
            rol = st.selectbox("Cargo", CARGOS, key=f"r{idx}")
            
            if st.button(f"▶ Procesar Lote {idx}", key=f"b{idx}"):
                if api_key and files:
                    execute_processing([{'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}], api_key, skip_dupes)
                else: st.warning("Falta API Key o archivos.")
            
            return {'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}

    b1 = render_batch_ui(col1, 1); b2 = render_batch_ui(col2, 2)
    b3 = render_batch_ui(col1, 3); b4 = render_batch_ui(col2, 4)
    
    st.divider()
    if st.button("🚀 PROCESAR TODOS LOS LOTES", type="primary", use_container_width=True):
        active_batches = [b for b in [b1, b2, b3, b4] if b['files']]
        if api_key and active_batches:
            execute_processing(active_batches, api_key, skip_dupes)
        else: st.warning("Falta API Key o no hay archivos.")

# --- TAB 2: Dashboard ---
with tab2:
    st.header("📊 Dashboard Ejecutivo")
    df = db_load_all()
    if df.empty:
        st.info("No hay datos para analizar.")
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric("Total", len(df)); k2.metric("Promedio Puntaje", f"{df['puntaje'].mean():.2f}")
        k3.metric("Aptos (Avanza)", len(df[df['recomendacion'] == 'Avanza']))
        
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Puntajes por Facultad")
            fig = px.box(df, x='facultad', y='puntaje', color='facultad'); st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.subheader("Distribución de Decisiones")
            fig2 = px.pie(df, names='recomendacion', hole=0.4); st.plotly_chart(fig2, use_container_width=True)

# --- TAB 3: Base de Datos ---
with tab3:
    st.header("🗃️ Base de Datos de Análisis (Streaming en Vivo)")
    st.info("Esta tabla se actualiza en tiempo real durante el procesamiento.")
    df_db = db_load_all()
    if df_db.empty:
        st.info("La base de datos está vacía.")
    else:
        st.dataframe(
            df_db[['timestamp', 'lote_nombre', 'candidato', 'facultad', 'cargo', 'puntaje', 'recomendacion']],
            column_config={
                "timestamp": "Fecha y Hora (HH:MM:SS)",
                "lote_nombre": "Lote",
                "puntaje": st.column_config.ProgressColumn("Puntaje", format="%.2f", min_value=0, max_value=5)
            }, use_container_width=True
        )

# --- TAB 4: Repositorio ---
with tab4:
    st.header("📂 Repositorio de Informes PDF")
    df_repo = db_load_all()
    if df_repo.empty:
        st.info("No hay informes generados.")
    else:
        c1, c2 = st.columns([1,2])
        zip_mem = io.BytesIO()
        with zipfile.ZipFile(zip_mem, "w") as zf:
            for i, row in df_repo.iterrows():
                if row['pdf_blob']: zf.writestr(f"{row['candidato']}.pdf", row['pdf_blob'])
        c1.download_button("📦 Descargar Todos (ZIP)", zip_mem.getvalue(), "Informes.zip", type="primary")
        c2.download_button("💾 Descargar Excel", df_repo.drop(columns=['pdf_blob', 'raw_json']).to_csv(index=False).encode('utf-8'), "data.csv")
        
        st.divider()
        for i, row in df_repo.iterrows():
            with st.expander(f"📄 {row['candidato']} - {row['cargo']} ({row['puntaje']})"):
                cols = st.columns([4, 1])
                cols[0].write(f"**Recomendación:** {row['recomendacion']}"); cols[0].caption(f"Procesado el {row['timestamp']}")
                if row['pdf_blob']: cols[1].download_button("Descargar", row['pdf_blob'], f"{row['candidato']}.pdf", key=f"dl_{i}")
