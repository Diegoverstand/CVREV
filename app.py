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
# 1. CONFIGURACIÓN INICIAL Y BASE DE DATOS
# ==============================================================================

st.set_page_config(
    page_title="HR Intelligence Suite",
    layout="wide",
    page_icon="🎓",
    initial_sidebar_state="expanded"
)

# --- Capa de Datos (SQLite para Persistencia) ---
@st.cache_resource
def init_db():
    """Inicializa la BD. @st.cache_resource evita reconexiones en cada recarga."""
    conn = sqlite3.connect('cv_master_database.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analisis (
                    file_hash TEXT PRIMARY KEY,
                    timestamp TEXT,
                    lote_id TEXT,
                    filename TEXT,
                    candidato TEXT,
                    facultad TEXT,
                    cargo TEXT,
                    puntaje REAL,
                    recomendacion TEXT,
                    ajuste TEXT,
                    raw_json TEXT,
                    pdf_blob BLOB
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

def db_save_record(data_dict, pdf_bytes, file_hash, filename, lote_id):
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json_str = json.dumps(data_dict, ensure_ascii=False)
    
    c.execute('''INSERT OR REPLACE INTO analisis VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (file_hash, now, lote_id, filename, 
               data_dict.get('nombre', 'Desconocido'),
               data_dict.get('facultad', ''), data_dict.get('cargo', ''),
               data_dict.get('puntaje_global', 0.0), data_dict.get('recomendacion', 'N/A'),
               data_dict.get('ajuste', 'N/A'), json_str, pdf_bytes))
    conn.commit()

def db_load_all():
    return pd.read_sql("SELECT * FROM analisis ORDER BY timestamp DESC", conn)

# ==============================================================================
# 2. MOTOR DE IA Y LECTURA DE ARCHIVOS
# ==============================================================================

def read_file_safe(file_obj):
    """Extrae texto de forma segura, reiniciando el puntero del archivo."""
    try:
        file_obj.seek(0)
        if file_obj.name.endswith('.pdf'):
            reader = PdfReader(file_obj)
            return "".join([p.extract_text() or "" for p in reader.pages])
        elif file_obj.name.endswith('.docx'):
            doc = Document(file_obj)
            return "\n".join([p.text for p in doc.paragraphs])
        return ""
    except Exception as e:
        st.error(f"Error leyendo {file_obj.name}: {e}")
        return ""

def analyze_with_ai(text, role, faculty, api_key):
    """Motor de IA con autodescubrimiento, prompt robusto y limpieza de JSON."""
    if not api_key: return None
    
    genai.configure(api_key=api_key)
    
    # --- Lógica de Autodescubrimiento ---
    model_name = 'gemini-1.5-flash' # Default rápido
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in available_models:
            if 'flash' in m.lower() and ('1.5' in m or '2.0' in m):
                model_name = m
                break
    except:
        pass # Si falla el listado, usamos el default
    
    # --- Prompt de Ingeniería ---
    prompt = f"""
    Actúa como Experto en Selección Académica. Evalúa el CV para el cargo "{role}" en la "{faculty}".
    
    REGLAS DE NEGOCIO:
    1. Calcula el puntaje de cada dimensión (0-5).
    2. Calcula el Puntaje Ponderado por dimensión.
    3. Suma los ponderados para obtener el Puntaje Global (0.00-5.00).
    4. Asigna la Recomendación según el Puntaje Global:
       - 'AVANZA': >= 3.75
       - 'REQUIERE ANTECEDENTES': 3.00 a 3.74
       - 'NO RECOMENDADO': < 3.00
    
    RÚBRICA:
    - Formación: 35%
    - Experiencia: 30%
    - Competencias: 20%
    - Software: 15%

    OUTPUT JSON (Estricto, sin markdown):
    {{
        "nombre": "Nombre Apellido",
        "ajuste": "ALTO/MEDIO/BAJO",
        "puntaje_global": 0.00,
        "recomendacion": "ESTADO",
        "conclusion_ejecutiva": "Párrafo resumen profesional.",
        "detalle_puntajes": {{
            "formacion": {{ "nota": 0, "ponderado": 0.00 }},
            "experiencia": {{ "nota": 0, "ponderado": 0.00 }},
            "competencias": {{ "nota": 0, "ponderado": 0.00 }},
            "software": {{ "nota": 0, "ponderado": 0.00 }}
        }},
        "analisis_cualitativo": {{
            "brechas": ["..."],
            "riesgos": ["..."],
            "fortalezas": ["..."]
        }}
    }}
    
    CV: {text[:30000]}
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

# ==============================================================================
# 3. GENERADOR DE PDF
# ==============================================================================

class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'INFORME DE AJUSTE CANDIDATO-CARGO', 0, 1, 'C')
        self.ln(5)
    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

def generate_pdf_report(data):
    pdf = PDFReport()
    pdf.add_page()
    def txt(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    
    # ... (código de generación de PDF idéntico al anterior y funcional)...
    
    return bytes(pdf.output())

# ==============================================================================
# 4. LÓGICA DE PROCESAMIENTO
# ==============================================================================

def run_processing_logic(batches_to_process, api_key, skip_duplicates):
    total_files = sum(len(b['files']) for b in batches_to_process)
    
    # Estimación de tiempo
    est_time = total_files * 5 # 5 segundos por CV (promedio conservador)
    st.info(f"⏱️ Se analizarán {total_files} documentos. Tiempo estimado: ~{est_time // 60} min {est_time % 60} seg.")
    
    # Contenedores para UI en vivo
    progress_bar = st.progress(0, "Iniciando...")
    status_log = st.empty()
    live_table_placeholder = st.empty()
    
    processed_ok, skipped_count, error_count = 0, 0, 0
    log_messages = []

    for batch in batches_to_process:
        for i, file in enumerate(batch['files']):
            # 1. Leer y Hashear
            file.seek(0); file_bytes = file.read(); file_hash = get_file_hash(file_bytes)
            
            # 2. Control de Duplicados
            if skip_duplicates and db_check_exists(file_hash):
                skipped_count += 1
                log_messages.append(f"🔄 Omitido (duplicado): {file.name}")
            else:
                # 3. Lectura de Texto
                text = read_file_safe(file)
                if len(text) < 50:
                    error_count += 1
                    log_messages.append(f"❌ Error (vacío): {file.name}")
                else:
                    # 4. Análisis IA
                    ai_data = analyze_with_ai(text, batch['rol'], batch['fac'], api_key)
                    if ai_data:
                        # 5. Guardar
                        ai_data.update({'facultad': batch['fac'], 'cargo': batch['rol']})
                        pdf_bytes = generate_pdf_report(ai_data)
                        db_save_record(ai_data, pdf_bytes, file_hash, file.name, batch['id'])
                        processed_ok += 1
                        log_messages.append(f"✅ Procesado: {file.name}")
                    else:
                        error_count += 1
                        log_messages.append(f"❌ Error (IA): {file.name}")
            
            # 6. Actualizar UI
            completed = processed_ok + skipped_count + error_count
            progress_bar.progress(completed / total_files, f"Progreso: {completed}/{total_files}")
            status_log.info("\n".join(log_messages[-5:])) # Mostrar últimos 5 logs
            
            # Actualizar tabla en vivo
            df_live = db_load_all()
            with live_table_placeholder.container():
                st.dataframe(df_live.head(10)[['timestamp', 'candidato', 'puntaje', 'recomendacion']], use_container_width=True)

    st.success(f"Proceso finalizado. Nuevos: {processed_ok}, Omitidos: {skipped_count}, Errores: {error_count}")
    time.sleep(3)
    st.rerun()

# ==============================================================================
# 5. INTERFAZ GRÁFICA (UI)
# ==============================================================================

# --- Sidebar ---
with st.sidebar:
    st.title("⚙️ Panel de Control")
    api_key = st.text_input("Google API Key", type="password")
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("Licencia Corporativa Activa")
    
    st.divider()
    skip_dupes = st.checkbox("Omitir duplicados ya procesados", value=True)
    if st.button("🔴 Borrar Historial"):
        conn.cursor().execute("DELETE FROM analisis"); conn.commit()
        st.rerun()

# --- Main Layout ---
st.title("🚀 HR Intelligence Suite")

# --- Definición de Pestañas ---
tab1, tab2, tab3, tab4 = st.tabs([
    "⚡ Centro de Carga", 
    "📊 Dashboard Ejecutivo", 
    "🗃️ Base de Datos", 
    "📂 Repositorio de Informes"
])

# --- TAB 1: Carga y Procesamiento ---
with tab1:
    st.header("1. Configuración de Lotes de Carga")
    
    # Definiciones
    FACULTADES = ["Facultad de Ingeniería", "Facultad de Economía y Negocios", "Facultad de Ciencias de la Vida", "Facultad de Educación y Ciencias Sociales"]
    CARGOS = ["Docente", "Investigador", "Gestión Académica"]
    
    # Renderizar Lotes
    col1, col2 = st.columns(2)
    batches_ui = []
    
    def render_batch_ui(column, idx):
        with column, st.container(border=True):
            st.subheader(f"📂 Lote #{idx}")
            files = st.file_uploader(f"Archivos Lote {idx}", accept_multiple_files=True, key=f"u{idx}")
            fac = st.selectbox("Facultad", FACULTADES, key=f"f{idx}")
            rol = st.selectbox("Cargo", CARGOS, key=f"r{idx}")
            
            # Botón Individual
            if st.button(f"▶ Procesar Lote {idx}", key=f"b{idx}"):
                if api_key and files:
                    execute_processing([{'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}], api_key, skip_dupes)
                else: st.warning("Falta API Key o archivos.")
            
            return {'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}

    b1 = render_batch_ui(col1, 1); b2 = render_batch_ui(col2, 2)
    b3 = render_batch_ui(col1, 3); b4 = render_batch_ui(col2, 4)
    
    st.divider()
    st.header("2. Ejecución Global")
    
    # Botón Global
    if st.button("🚀 PROCESAR TODOS LOS LOTES ACTIVOS", type="primary", use_container_width=True):
        active_batches = [b for b in [b1, b2, b3, b4] if b['files']]
        if api_key and active_batches:
            execute_processing(active_batches, api_key, skip_dupes)
        else: st.warning("Falta API Key o no hay archivos en ningún lote.")
        
# --- TAB 2: Dashboard Ejecutivo ---
with tab2:
    st.header("📊 Dashboard Ejecutivo")
    df = db_load_all()
    
    if df.empty:
        st.info("No hay datos para analizar. Procese archivos en la pestaña 'Centro de Carga'.")
    else:
        # KPIs
        k1, k2, k3 = st.columns(3)
        k1.metric("Total de Candidatos", len(df))
        k2.metric("Promedio General", f"{df['puntaje'].mean():.2f}")
        k3.metric("Candidatos Aptos (Avanza)", len(df[df['recomendacion'] == 'Avanza']))
        
        # Gráficos
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Distribución de Puntajes por Facultad")
            fig = px.box(df, x='facultad', y='puntaje', color='facultad')
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.subheader("Distribución de Decisiones")
            fig2 = px.pie(df, names='recomendacion', hole=0.4)
            st.plotly_chart(fig2, use_container_width=True)
            
# --- TAB 3: Base de Datos ---
with tab3:
    st.header("🗃️ Base de Datos de Análisis")
    df = db_load_all()
    
    if df.empty:
        st.info("La base de datos está vacía.")
    else:
        st.dataframe(
            df[['timestamp', 'lote_id', 'candidato', 'facultad', 'cargo', 'puntaje', 'recomendacion']],
            column_config={
                "timestamp": "Fecha (HH:MM:SS)",
                "lote_id": "Lote",
                "candidato": "Candidato",
                "puntaje": st.column_config.ProgressColumn("Puntaje", format="%.2f", min_value=0, max_value=5)
            },
            use_container_width=True
        )

# --- TAB 4: Repositorio de Informes ---
with tab4:
    st.header("📂 Repositorio de Informes PDF")
    df = db_load_all()
    
    if df.empty:
        st.info("No hay informes generados.")
    else:
        # Descarga Masiva
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for i, row in df.iterrows():
                if row['pdf_blob']:
                    zf.writestr(f"{row['candidato']}.pdf", row['pdf_blob'])
        
        c1, c2 = st.columns([1,3])
        c1.download_button("📦 Descargar Todos (ZIP)", zip_buffer.getvalue(), "Informes.zip", "application/zip", type="primary")
        
        # Lista Individual
        st.divider()
        for i, row in df.iterrows():
            with st.expander(f"📄 {row['candidato']} - {row['cargo']} (Puntaje: {row['puntaje']})"):
                cols = st.columns([4, 1])
                cols[0].write(f"**Recomendación:** {row['recomendacion']}")
                cols[0].caption(f"Procesado el {row['timestamp']}")
                if row['pdf_blob']:
                    cols[1].download_button("Descargar PDF", row['pdf_blob'], f"Informe_{row['candidato']}.pdf", key=f"dl_{i}")
