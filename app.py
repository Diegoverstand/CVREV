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

# Estilos CSS (Sin alterar colores de fondo para evitar problemas de contraste)
st.markdown("""
    <style>
    .main .block-container { padding-top: 1rem; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; }
    div[data-testid="stExpander"] { border: 1px solid #4a4a4a; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. GESTIÓN DE API Y MODELOS (AUTODESCUBRIMIENTO)
# ==============================================================================

def get_api_key():
    """Gestiona la obtención de la API Key priorizando Secrets."""
    # 1. Intentar cargar desde Secrets
    if 'GOOGLE_API_KEY' in st.secrets:
        return st.secrets['GOOGLE_API_KEY'], True # True = Es corporativa
    
    # 2. Si no hay secret, buscar en input de sesión
    return st.session_state.get('user_api_key', ''), False

def get_available_models(api_key):
    """Consulta a Google qué modelos están realmente disponibles."""
    if not api_key: return []
    
    genai.configure(api_key=api_key)
    try:
        model_list = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                # Limpiamos el nombre (ej: models/gemini-1.5-flash -> gemini-1.5-flash)
                name = m.name.replace('models/', '')
                model_list.append(name)
        
        # Ordenamos para que los 'flash' aparezcan primero (más rápidos)
        model_list.sort(key=lambda x: 'flash' not in x)
        return model_list
    except Exception as e:
        return ["gemini-1.5-flash"] # Fallback seguro si falla la lista

# ==============================================================================
# 3. BASE DE DATOS (PERSISTENCIA)
# ==============================================================================

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
    puntaje = float(data_dict.get('puntaje_global', 0.0))
    
    c.execute('''INSERT OR REPLACE INTO analisis VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (file_hash, now, lote_name, filename, 
               data_dict.get('nombre', 'Desconocido'),
               data_dict.get('facultad', ''), data_dict.get('cargo', ''),
               puntaje, data_dict.get('recomendacion', 'N/A'),
               data_dict.get('ajuste', 'N/A'), json_str, pdf_bytes))
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
    - Puntaje Global: 0.00 a 5.00
    - Recomendación: "AVANZA" (>=3.75), "REQUIERE ANTECEDENTES" (3.00-3.74), "NO RECOMENDADO" (<3.00)
    
    OUTPUT JSON:
    {{
        "nombre": "Nombre Apellido",
        "ajuste": "ALTO/MEDIO/BAJO",
        "puntaje_global": 0.00,
        "recomendacion": "ESTADO",
        "conclusion_ejecutiva": "Resumen.",
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
        for i in items: pdf.multi_cell(0, 5, txt(f"- {i}"))
        pdf.ln(2)
        
    return bytes(pdf.output())

# ==============================================================================
# 6. INTERFAZ DE USUARIO (SIDEBAR Y NAVEGACIÓN)
# ==============================================================================

# Obtener API Key y Estado
api_key, is_corporate = get_api_key()

with st.sidebar:
    st.header("⚙️ Configuración")
    
    # 1. API Key (Solo se muestra si NO es corporativa)
    if is_corporate:
        st.success("✅ Licencia Corporativa Activa")
    else:
        user_input = st.text_input("Google API Key", type="password", help="Ingrese su clave si no tiene licencia corporativa.")
        if user_input:
            st.session_state['user_api_key'] = user_input
            api_key = user_input
            st.experimental_rerun()

    st.divider()
    
    # 2. Selector de Modelo (Dinámico)
    st.subheader("🧠 Modelo de IA")
    if api_key:
        available_models = get_available_models(api_key)
        if available_models:
            model_choice = st.selectbox("Modelo Detectado:", available_models, index=0)
        else:
            st.error("No se encontraron modelos. Verifique su API Key.")
            model_choice = None
    else:
        st.warning("Ingrese API Key para cargar modelos.")
        model_choice = None
        
    # Rate Limit Manual
    req_delay = st.slider("Pausa entre análisis (seg)", 2, 20, 5, help="Aumentar para evitar errores de cuota.")
    
    st.divider()
    skip_dupes = st.checkbox("Omitir Duplicados", value=True)
    if st.button("🗑️ Borrar Historial"):
        conn.cursor().execute("DELETE FROM analisis"); conn.commit(); st.rerun()

st.title("🚀 HR Intelligence Suite")

# TABS
tab1, tab2, tab3, tab4 = st.tabs(["📥 Centro de Carga", "📊 Dashboard", "🗃️ Base de Datos", "📂 Repositorio"])

FACULTADES = ["Facultad de Ingeniería", "Facultad de Economía y Negocios", "Facultad de Ciencias de la Vida", "Facultad de Educación y Ciencias Sociales"]
CARGOS = ["Docente", "Investigador", "Gestión Académica"]

# --- LÓGICA DE PROCESAMIENTO ---
def run_processing(batches):
    total_docs = sum(len(b['files']) for b in batches)
    st.info(f"Iniciando análisis de {total_docs} documentos...")
    
    prog_bar = st.progress(0)
    status_box = st.empty()
    live_table_box = st.empty()
    
    processed, skipped, errors = 0, 0, 0
    current = 0
    
    for b in batches:
        for f in b['files']:
            current += 1
            status_box.text(f"Procesando {current}/{total_docs}: {f.name}")
            
            # Hash
            f.seek(0); f_bytes = f.read(); f_hash = get_file_hash(f_bytes)
            
            if skip_dupes and db_check_exists(f_hash):
                skipped += 1
            else:
                f.seek(0); text = read_file_safe(f)
                if len(text) > 50:
                    ai_res = analyze_with_gemini(text, b['rol'], b['fac'], api_key, model_choice)
                    if ai_res:
                        ai_res.update({'facultad': b['fac'], 'cargo': b['rol']})
                        pdf = generate_pdf_report(ai_res)
                        db_save_record(ai_res, pdf, f_hash, f.name, b['id'])
                        processed += 1
                        time.sleep(req_delay)
                    else: errors += 1
                else: errors += 1
            
            prog_bar.progress(current / total_docs)
            
            # Update Tabla en Vivo
            if current % 1 == 0:
                with live_table_box.container():
                    df = db_load_all()
                    if not df.empty: st.dataframe(df.head(5)[['candidato', 'puntaje', 'recomendacion']], use_container_width=True)

    status_box.success(f"Fin: {processed} Nuevos | {skipped} Duplicados | {errors} Errores")
    time.sleep(2); st.rerun()

# --- TAB 1: CARGA ---
with tab1:
    c1, c2 = st.columns(2)
    batches_data = []
    
    def render_batch(col, idx):
        with col, st.container(border=True):
            st.subheader(f"📂 Lote #{idx}")
            files = st.file_uploader(f"Docs {idx}", accept_multiple_files=True, key=f"u{idx}")
            fac = st.selectbox("Facultad", FACULTADES, key=f"f{idx}")
            rol = st.selectbox("Cargo", CARGOS, key=f"r{idx}")
            
            if st.button(f"▶ Procesar Lote {idx}", key=f"b{idx}"):
                if api_key and model_choice and files:
                    run_processing([{'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}])
                else: st.error("Verifique Configuración")
            
            if files: batches_data.append({'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol})

    render_batch(c1, 1); render_batch(c2, 2)
    render_batch(c1, 3); render_batch(c2, 4)
    
    st.markdown("---")
    if st.button("🚀 PROCESAR TODO", type="primary", use_container_width=True):
        if api_key and model_choice and batches_data: run_processing(batches_data)
        else: st.error("Faltan datos o API Key")

# --- TAB 2: DASHBOARD ---
with tab2:
    st.header("📊 Analytics")
    df = db_load_all()
    if not df.empty:
        k1, k2, k3 = st.columns(3)
        k1.metric("Total", len(df)); k2.metric("Promedio", f"{df['puntaje'].mean():.2f}")
        k3.metric("Aptos", len(df[df['recomendacion'] == "AVANZA"]))
        
        g1, g2 = st.columns(2)
        with g1: st.plotly_chart(px.box(df, x='facultad', y='puntaje', color='facultad'), use_container_width=True)
        with g2: st.plotly_chart(px.pie(df, names='recomendacion'), use_container_width=True)
    else: st.info("Sin datos.")

# --- TAB 3: DATOS ---
with tab3:
    st.header("🗃️ Base de Datos")
    df = db_load_all()
    if not df.empty:
        st.dataframe(df[['timestamp', 'lote_nombre', 'candidato', 'puntaje', 'recomendacion']], use_container_width=True)
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
