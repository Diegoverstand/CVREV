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
# 1. CONFIGURACIÓN DEL ENTORNO
# ==============================================================================

st.set_page_config(
    page_title="HR Intelligence Suite",
    layout="wide",
    page_icon="🎓",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .main .block-container { padding-top: 1rem; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; }
    div[data-testid="stExpander"] { border: 1px solid #4a4a4a; border-radius: 5px; }
    
    /* Ajuste para botón de limpiar pequeño */
    .small-btn { margin-top: 0px; }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. GESTIÓN DE API Y MODELOS
# ==============================================================================

def get_api_key():
    if 'GOOGLE_API_KEY' in st.secrets:
        return st.secrets['GOOGLE_API_KEY'], True
    return st.session_state.get('user_api_key', ''), False

def get_available_models(api_key):
    if not api_key: return []
    genai.configure(api_key=api_key)
    try:
        model_list = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                name = m.name.replace('models/', '')
                model_list.append(name)
        model_list.sort(key=lambda x: 'flash' not in x.lower())
        return model_list
    except:
        return ["gemini-1.5-flash"]

# ==============================================================================
# 3. BASE DE DATOS
# ==============================================================================

@st.cache_resource
def init_db():
    conn = sqlite3.connect('cv_final_db_v4.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analisis (
                    file_hash TEXT PRIMARY KEY, timestamp TEXT, lote_nombre TEXT, archivo_nombre TEXT,
                    candidato TEXT, facultad TEXT, cargo TEXT, puntaje REAL, recomendacion TEXT,
                    ajuste TEXT, comentarios TEXT, raw_json TEXT, pdf_blob BLOB
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
    puntaje = float(data_dict.get('puntaje_global', 0.0))
    
    # Extraemos el resumen ejecutivo para la tabla
    comentarios = data_dict.get('conclusion_ejecutiva', 'Sin comentarios')
    
    c.execute('''INSERT OR REPLACE INTO analisis VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (file_hash, now, lote_name, filename, 
               data_dict.get('nombre', 'Desconocido'),
               data_dict.get('facultad', ''), data_dict.get('cargo', ''),
               puntaje, data_dict.get('recomendacion', 'N/A'),
               data_dict.get('ajuste', 'N/A'), comentarios, json_str, pdf_bytes))
    conn.commit()

def db_load_all():
    return pd.read_sql("SELECT * FROM analisis ORDER BY timestamp DESC", conn)

# ==============================================================================
# 4. MOTORES DE ANÁLISIS
# ==============================================================================

def read_file_safe(file_obj):
    try:
        file_obj.seek(0)
        if file_obj.name.lower().endswith('.pdf'):
            reader = PdfReader(file_obj)
            return "".join([p.extract_text() or "" for p in reader.pages])
        elif file_obj.name.lower().endswith('.docx'):
            doc = Document(file_obj)
            return "\n".join([p.text for p in doc.paragraphs])
        return ""
    except: return ""

def analyze_with_gemini(text, role, faculty, api_key, model_choice):
    if not api_key: return None
    genai.configure(api_key=api_key)
    
    prompt = f"""
    Actúa como Consultor de Selección. Evalúa CV para: {role} - {faculty}.
    
    RÚBRICA:
    1. Formación (35%)
    2. Experiencia (30%)
    3. Competencias (20%)
    4. Software (15%)
    
    REGLAS:
    - Puntaje Global: 0.00 a 5.00 (Dos decimales)
    - Recomendación: "AVANZA" (>=3.75), "REQUIERE ANTECEDENTES" (3.00-3.74), "NO RECOMENDADO" (<3.00)
    
    OUTPUT JSON (Estricto):
    {{
        "nombre": "Nombre Apellido",
        "ajuste": "ALTO/MEDIO/BAJO",
        "puntaje_global": 0.00,
        "recomendacion": "ESTADO",
        "conclusion_ejecutiva": "Resumen breve de maximo 40 palabras.",
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
    CV: {text[:30000]}
    """
    
    try:
        model = genai.GenerativeModel(model_choice)
        response = model.generate_content(prompt)
        raw = response.text
        start = raw.find('{')
        end = raw.rfind('}') + 1
        return json.loads(raw[start:end]) if start != -1 else None
    except Exception as e:
        print(f"Error IA: {e}")
        return None

# ==============================================================================
# 5. GENERADOR PDF
# ==============================================================================

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
    pdf.cell(0, 6, txt(data.get('nombre', 'N/A')), 0, 1)
    pdf.set_font('Arial', 'B', 11); pdf.cell(30, 6, "Cargo:", 0, 0); pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, txt(f"{data.get('cargo')} - {data.get('facultad')}"), 0, 1); pdf.ln(8)
    
    # Conclusión
    pdf.set_font('Arial', 'B', 12); pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("A. CONCLUSIÓN EJECUTIVA"), 1, 1, 'L', True); pdf.ln(2)
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 5, txt(f"Ajuste: {data.get('ajuste')}. Puntaje: {data.get('puntaje_global', 0):.2f}/5.00.\n{data.get('conclusion_ejecutiva')}"))
    pdf.ln(5)
    
    # Semáforo
    rec = data.get('recomendacion', '').upper()
    if "NO" in rec: pdf.set_fill_color(255, 200, 200)
    elif "AVANZA" in rec: pdf.set_fill_color(200, 255, 200)
    else: pdf.set_fill_color(255, 255, 200)
    pdf.set_font('Arial', 'B', 12); pdf.cell(0, 10, txt(rec), 1, 1, 'C', True); pdf.ln(10)
    
    # Tabla
    pdf.set_font('Arial', 'B', 12); pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("B. TABLA RESUMEN"), 1, 1, 'L', True)
    pdf.set_font('Arial', 'B', 9); pdf.set_fill_color(50, 50, 50); pdf.set_text_color(255)
    pdf.cell(80, 8, "Dimensión", 1, 0, 'L', True); pdf.cell(30, 8, "Ponderación", 1, 0, 'C', True)
    pdf.cell(30, 8, "Nota", 1, 0, 'C', True); pdf.cell(50, 8, "Puntaje", 1, 1, 'C', True)
    pdf.set_text_color(0); pdf.set_font('Arial', '', 9)
    
    det = data.get('detalle_puntajes', {})
    dims = [("Formación", "35%", det.get('formacion', {})), ("Experiencia", "30%", det.get('experiencia', {})),
            ("Competencias", "20%", det.get('competencias', {})), ("Software", "15%", det.get('software', {}))]
    
    for n, p, v in dims:
        pdf.ln(8); pdf.cell(80, 8, txt(n), 1); pdf.cell(30, 8, p, 1, 0, 'C')
        pdf.cell(30, 8, str(v.get('nota', 0)), 1, 0, 'C'); pdf.cell(50, 8, f"{v.get('ponderado', 0):.2f}", 1, 0, 'C')
    pdf.ln(8)
    pdf.set_fill_color(230,230,230); pdf.set_font('Arial', 'B', 9)
    pdf.cell(140, 8, "TOTAL PONDERADO", 1, 0, 'R', True); pdf.cell(50, 8, f"{data.get('puntaje_global', 0):.2f}", 1, 1, 'C', True)
    pdf.ln(10)
    
    # Cualitativo
    pdf.set_font('Arial', 'B', 12); pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("C. ANÁLISIS CUALITATIVO"), 1, 1, 'L', True); pdf.ln(2)
    pdf.set_font('Arial', '', 10)
    
    cual = data.get('analisis_cualitativo', {})
    for k, v in cual.items():
        pdf.set_font('Arial', 'B', 10); pdf.cell(0, 6, txt(k.capitalize()), 0, 1); pdf.set_font('Arial', '', 10)
        items = v if isinstance(v, list) else [str(v)]
        for i in items: 
            pdf.set_x(10)
            pdf.multi_cell(190, 5, txt(f"- {i}"))
        pdf.ln(2)
    
    return bytes(pdf.output())

# ==============================================================================
# 6. LÓGICA DE PROCESAMIENTO
# ==============================================================================

def execute_processing(batches, api_key, model_choice, skip_dupes, delay_sec):
    total_files = sum(len(b['files']) for b in batches)
    
    # AVISO DE TIEMPO (REQUERIMIENTO CLAVE)
    est_time_min = (total_files * (delay_sec + 5)) / 60
    st.info(f"⏱️ **Estimación:** Se procesarán {total_files} documentos. Tiempo aprox: {est_time_min:.1f} minutos. Por favor espere...")
    
    # UI Containers
    progress_bar = st.progress(0, "Iniciando motor de análisis...")
    status = st.empty()
    st.subheader("📋 Datos en Vivo (Streaming)")
    live_table = st.empty()
    
    processed, skipped, errors = 0, 0, 0
    current_idx = 0
    
    for batch in batches:
        for file in batch['files']:
            current_idx += 1
            status.text(f"Analizando {current_idx}/{total_files}: {file.name} ({batch['id']})...")
            
            # 1. Duplicidad
            file.seek(0); f_bytes = file.read(); f_hash = get_file_hash(f_bytes)
            
            if skip_dupes and db_check_exists(f_hash):
                skipped += 1
            else:
                # 2. Análisis
                file.seek(0)
                text = read_file_safe(file)
                if len(text) > 50:
                    ai_res = analyze_with_gemini(text, batch['rol'], batch['fac'], api_key, model_choice)
                    if ai_res:
                        ai_res.update({'facultad': batch['fac'], 'cargo': batch['rol']})
                        pdf = generate_pdf_report(ai_res)
                        db_save_record(ai_res, pdf, f_hash, file.name, batch['id'])
                        processed += 1
                    else: errors += 1
                else: errors += 1
                
                # 3. Rate Limit
                time.sleep(delay_sec)
            
            # 4. Actualizar UI
            progress_bar.progress(current_idx / total_files)
            
            # STREAMING EN VIVO DE LA TABLA COMPLETA
            with live_table.container():
                df = db_load_all()
                if not df.empty:
                    st.dataframe(
                        df[['timestamp', 'lote_nombre', 'candidato', 'puntaje', 'recomendacion', 'comentarios']],
                        column_config={
                            "timestamp": st.column_config.DatetimeColumn("Hora", format="HH:mm:ss"),
                            "puntaje": st.column_config.ProgressColumn("Puntaje", format="%.2f", min_value=0, max_value=5),
                            "comentarios": st.column_config.TextColumn("Resumen Ejecutivo", width="large")
                        },
                        use_container_width=True,
                        hide_index=True
                    )

    status.success(f"Finalizado. Nuevos: {processed} | Saltados: {skipped} | Errores: {errors}")
    time.sleep(2)
    st.rerun()

#Función helper para limpiar lote
def clear_batch_state(key):
    if key in st.session_state:
        del st.session_state[key]
    st.rerun()

# ==============================================================================
# 7. INTERFAZ GRÁFICA
# ==============================================================================

api_key, is_corporate = get_api_key()

with st.sidebar:
    st.header("⚙️ Configuración")
    
    if is_corporate:
        st.success("✅ Licencia Corporativa Activa")
    else:
        user_input = st.text_input("Google API Key", type="password")
        if user_input:
            st.session_state['user_api_key'] = user_input
            api_key = user_input
            st.rerun()

    st.divider()
    
    st.subheader("🧠 Modelo de IA")
    if api_key:
        available_models = get_available_models(api_key)
        if available_models:
            model_choice = st.selectbox("Modelo Detectado:", available_models, index=0)
        else:
            st.error("Error al cargar modelos.")
            model_choice = None
    else:
        st.warning("Ingrese API Key.")
        model_choice = None
        
    delay = st.slider("Pausa entre análisis (seg)", 2, 20, 5)
    
    st.divider()
    skip_dupes = st.checkbox("Omitir Duplicados", value=True)
    if st.button("🗑️ Borrar Historial"):
        conn.cursor().execute("DELETE FROM analisis"); conn.commit(); st.rerun()

st.title("🚀 HR Intelligence Suite")

tab1, tab2, tab3, tab4 = st.tabs(["📥 Centro de Carga", "📊 Dashboard", "🗃️ Base de Datos", "📂 Repositorio"])

FACULTADES = ["Facultad de Ingeniería", "Facultad de Economía y Negocios", "Facultad de Ciencias de la Vida", "Facultad de Educación y Ciencias Sociales"]
CARGOS = ["Docente", "Investigador", "Gestión Académica"]

# --- TAB 1: Carga ---
with tab1:
    c1, c2 = st.columns(2)
    batches_data = []
    
    def render_batch(col, idx):
        with col, st.container(border=True):
            # Header con Botón de Limpiar
            sub_c1, sub_c2 = st.columns([4, 1])
            sub_c1.subheader(f"📂 Lote #{idx}")
            if sub_c2.button("🗑️", key=f"clr{idx}", help="Limpiar archivos de este lote"):
                clear_batch_state(f"u{idx}")

            files = st.file_uploader(f"Docs {idx}", accept_multiple_files=True, key=f"u{idx}", label_visibility="collapsed")
            fac = st.selectbox("Facultad", FACULTADES, key=f"f{idx}")
            rol = st.selectbox("Cargo", CARGOS, key=f"r{idx}")
            
            if st.button(f"▶ Procesar Lote {idx}", key=f"b{idx}"):
                if api_key and model_choice and files:
                    execute_processing([{'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}], api_key, model_choice, skip_dupes, delay)
                else: st.error("Verifique Configuración")
            
            if files: batches_data.append({'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol})

    render_batch(c1, 1); render_batch(c2, 2)
    render_batch(c1, 3); render_batch(c2, 4)
    
    st.markdown("---")
    if st.button("🚀 PROCESAR TODO", type="primary", use_container_width=True):
        if api_key and model_choice and batches_data:
            execute_processing(batches_data, api_key, model_choice, skip_dupes, delay)
        else: st.error("Faltan datos o API Key")

# --- TAB 2: DASHBOARD ---
with tab2:
    st.header("📊 Analytics")
    df = db_load_all()
    if not df.empty:
        k1, k2, k3 = st.columns(3)
        k1.metric("Total", len(df)); k2.metric("Promedio", f"{df['puntaje'].mean():.2f}")
        k3.metric("Aptos", len(df[df['recomendacion'].str.contains("AVANZA")]))
        
        g1, g2 = st.columns(2)
        with g1: st.plotly_chart(px.box(df, x='facultad', y='puntaje', color='facultad'), use_container_width=True)
        with g2: st.plotly_chart(px.pie(df, names='recomendacion'), use_container_width=True)
    else: st.info("Sin datos.")

# --- TAB 3: DATOS ---
with tab3:
    st.header("🗃️ Base de Datos Completa")
    df = db_load_all()
    if not df.empty:
        st.dataframe(
            df[['timestamp', 'lote_nombre', 'candidato', 'puntaje', 'recomendacion', 'comentarios']],
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Hora", format="HH:mm:ss"),
                "puntaje": st.column_config.ProgressColumn("Puntaje", format="%.2f", min_value=0, max_value=5),
                "comentarios": st.column_config.TextColumn("Resumen Ejecutivo", width="large")
            },
            use_container_width=True,
            hide_index=True
        )
        st.download_button("Descargar Excel", df.drop(columns=['pdf_blob', 'raw_json']).to_csv(index=False).encode('utf-8'), "data.csv")
    else: st.info("Vacía.")

# --- TAB 4: REPOSITORIO ---
with tab4:
    st.header("📂 Informes")
    df = db_load_all()
    if not df.empty:
        zip_mem = io.BytesIO()
        with zipfile.ZipFile(zip_mem, "w") as zf:
            for i, r in df.iterrows():
                if r['pdf_blob']: zf.writestr(f"{r['candidato']}.pdf", r['pdf_blob'])
        st.download_button("📦 Descargar ZIP", zip_mem.getvalue(), "Informes.zip", type="primary")
        
        for i, r in df.iterrows():
            with st.expander(f"{r['candidato']} ({r['puntaje']})"):
                if r['pdf_blob']: st.download_button("PDF", r['pdf_blob'], f"{r['candidato']}.pdf", key=f"d{i}")
    else: st.info("Sin informes.")
