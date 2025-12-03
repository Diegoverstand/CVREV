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
# 1. CONFIGURACIÓN DEL SISTEMA Y ESTILOS
# ==============================================================================

st.set_page_config(
    page_title="HR Intelligence Suite Pro",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="expanded"
)

# Estilos CSS Corporativos (Dark Mode)
st.markdown("""
    <style>
    /* Fondo y Textos */
    .stApp { background-color: #0E1117; }
    h1, h2, h3, h4, h5, h6 { color: #FAFAFA !important; font-family: 'Segoe UI', sans-serif; }
    p, label, span, div { color: #E0E0E0; }
    
    /* Tarjetas de Lotes */
    .batch-card {
        background-color: #262730;
        border: 1px solid #41444C;
        border-radius: 8px;
        padding: 15px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
    }
    .batch-title {
        font-size: 1.1rem;
        font-weight: bold;
        color: white !important;
        border-bottom: 1px solid #555;
        padding-bottom: 8px;
        margin-bottom: 10px;
        display: block;
    }
    
    /* Bordes Identificadores */
    .b-blue { border-top: 4px solid #3498db; }
    .b-green { border-top: 4px solid #2ecc71; }
    .b-orange { border-top: 4px solid #e67e22; }
    .b-purple { border-top: 4px solid #9b59b6; }

    /* Botones */
    .stButton>button {
        width: 100%;
        border-radius: 4px;
        font-weight: 600;
        text-transform: uppercase;
        transition: 0.2s;
    }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. BASE DE DATOS (PERSISTENCIA)
# ==============================================================================

def init_db():
    conn = sqlite3.connect('cv_database.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cv_history (
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
                    json_data TEXT,
                    pdf_blob BLOB
                )''')
    conn.commit()
    return conn

conn = init_db()

def get_file_hash(file_obj):
    file_obj.seek(0)
    data = file_obj.read()
    file_obj.seek(0)
    return hashlib.md5(data).hexdigest()

def check_history(file_hash):
    c = conn.cursor()
    c.execute("SELECT candidato FROM cv_history WHERE file_hash = ?", (file_hash,))
    return c.fetchone()

def save_result(data_dict, pdf_bytes, file_hash, filename, lote_id):
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json_str = json.dumps(data_dict, ensure_ascii=False)
    
    c.execute('''INSERT OR REPLACE INTO cv_history 
                 (file_hash, timestamp, lote_id, filename, candidato, facultad, cargo, puntaje, recomendacion, ajuste, json_data, pdf_blob)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (file_hash, now, lote_id, filename, data_dict.get('nombre', 'N/A'), 
               data_dict.get('facultad'), data_dict.get('cargo'), data_dict.get('puntaje_global', 0),
               data_dict.get('recomendacion'), data_dict.get('ajuste'), json_str, pdf_bytes))
    conn.commit()

def get_dataframe():
    return pd.read_sql("SELECT * FROM cv_history ORDER BY timestamp DESC", conn)

# ==============================================================================
# 3. MOTORES IA Y LECTURA
# ==============================================================================

def read_file_content(file):
    try:
        file.seek(0)
        if file.type == "application/pdf":
            reader = PdfReader(file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        elif "word" in file.type or file.name.endswith(".docx"):
            doc = Document(file)
            return "\n".join([p.text for p in doc.paragraphs])
        return ""
    except Exception: return ""

def analyze_with_ai(text, role, faculty, api_key):
    if not api_key: return None
    genai.configure(api_key=api_key)
    
    # Rúbrica Base
    base_rubrica = {"Formación": "35%", "Experiencia": "30%", "Competencias": "20%", "Software": "15%"}
    
    prompt = f"""
    Actúa como Experto en Selección Académica. Evalúa CV para Cargo: "{role}" en Facultad: "{faculty}".
    RÚBRICA: {json.dumps(base_rubrica)}
    
    INSTRUCCIONES:
    1. Responde SOLO JSON válido.
    2. "puntaje_global" float 2 decimales (1.00-5.00).
    3. "recomendacion" UNA DE: "NO RECOMENDADO", "REQUIERE ANTECEDENTES", "AVANZA".
    
    JSON:
    {{
        "nombre": "Nombre Apellido",
        "ajuste": "ALTO/MEDIO/BAJO",
        "puntaje_global": 0.00,
        "recomendacion": "ESTADO",
        "conclusion_ejecutiva": "Resumen.",
        "detalle": {{
            "formacion": {{ "nota": 0, "ponderado": 0.00 }},
            "experiencia": {{ "nota": 0, "ponderado": 0.00 }},
            "competencias": {{ "nota": 0, "ponderado": 0.00 }},
            "software": {{ "nota": 0, "ponderado": 0.00 }}
        }},
        "cualitativo": {{
            "brechas": ["p1"],
            "riesgos": ["p1"],
            "fortalezas": ["p1"]
        }}
    }}
    CV: {text[:25000]}
    """
    
    # Intento con modelos disponibles (Auto-descubrimiento simplificado)
    models = ['gemini-2.0-flash-exp', 'gemini-1.5-flash', 'gemini-1.5-pro']
    response = None
    for m in models:
        try:
            model = genai.GenerativeModel(m)
            response = model.generate_content(prompt)
            break
        except: continue
        
    if not response: return None

    try:
        raw = response.text
        start, end = raw.find('{'), raw.rfind('}') + 1
        return json.loads(raw[start:end]) if start != -1 else None
    except: return None

# ==============================================================================
# 4. GENERADOR PDF
# ==============================================================================

class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 16)
        self.cell(0, 10, 'INFORME DE AJUSTE CANDIDATO-CARGO', 0, 1, 'C')
        self.ln(10)
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, f'Generado: {datetime.now().strftime("%d/%m/%Y")}', 0, 0, 'C')

def generate_pdf(data):
    pdf = PDFReport()
    pdf.add_page()
    def txt(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    
    # Encabezado
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(30, 6, "Cargo:", 0, 0)
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, txt(f"{data['cargo']} - {data['facultad']}"), 0, 1)
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(30, 6, "Candidato:", 0, 0)
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, txt(data['nombre']), 0, 1)
    pdf.ln(8)
    
    # Conclusión
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("A. CONCLUSIÓN EJECUTIVA"), 1, 1, 'L', True)
    pdf.ln(2)
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 5, txt(f"Ajuste: {data['ajuste']}. Puntaje: {data['puntaje_global']}/5.00.\n{data['conclusion_ejecutiva']}"))
    pdf.ln(5)
    
    # Caja Recomendación
    rec = data['recomendacion'].upper()
    if "NO" in rec:
        pdf.set_fill_color(255, 200, 200); pdf.set_text_color(150, 0, 0)
    elif "AVANZA" in rec:
        pdf.set_fill_color(200, 255, 200); pdf.set_text_color(0, 100, 0)
    else:
        pdf.set_fill_color(255, 255, 200); pdf.set_text_color(100, 100, 0)
        
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 12, txt(rec), 1, 1, 'C', True)
    pdf.set_text_color(0)
    pdf.ln(8)
    
    # Tabla
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("B. TABLA RESUMEN"), 1, 1, 'L', True)
    
    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(50, 50, 50); pdf.set_text_color(255)
    pdf.cell(80, 8, "Dimensión", 1, 0, 'L', True)
    pdf.cell(30, 8, "Ponderación", 1, 0, 'C', True)
    pdf.cell(30, 8, "Nota", 1, 0, 'C', True)
    pdf.cell(50, 8, "Ponderado", 1, 1, 'C', True)
    
    pdf.set_text_color(0); pdf.set_font('Arial', '', 9)
    det = data['detalle']
    dims = [("Formación", "35%", det.get('formacion', {})), ("Experiencia", "30%", det.get('experiencia', {})),
            ("Competencias", "20%", det.get('competencias', {})), ("Software", "15%", det.get('software', {}))]
    
    for n, p, v in dims:
        pdf.cell(80, 7, txt(n), 1)
        pdf.cell(30, 7, p, 1, 0, 'C')
        pdf.cell(30, 7, str(v.get('nota', 0)), 1, 0, 'C')
        pdf.cell(50, 7, f"{v.get('ponderado', 0):.2f}", 1, 1, 'C')
    
    pdf.ln(5)
    
    # Cualitativo
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("C. COMENTARIOS FINALES"), 1, 1, 'L', True)
    pdf.ln(2)
    
    cual = data['cualitativo']
    for k, v in cual.items():
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 6, txt(k.upper()), 0, 1)
        pdf.set_font('Arial', '', 10)
        for i in v:
            pdf.cell(5, 5, "-", 0, 0)
            pdf.multi_cell(0, 5, txt(str(i)))
    
    return bytes(pdf.output())

# ==============================================================================
# 5. LOGICA DE PROCESAMIENTO
# ==============================================================================

def execute_processing(batches, api_key, skip_dupes):
    total_files = sum([len(b['files']) for b in batches])
    st.info(f"⏱️ Procesando {total_files} archivos. Estimado: {total_files * 4 // 60} min.")
    
    progress = st.progress(0)
    status = st.empty()
    table_ph = st.empty()
    
    processed, skipped, errors = 0, 0, 0
    
    for b in batches:
        for f in b['files']:
            status.text(f"Analizando: {f.name} ({b['id']})...")
            f_hash = get_file_hash(f)
            
            if check_history(f_hash) and skip_dupes:
                skipped += 1
            else:
                text = read_file_content(f)
                if len(text) > 50:
                    res = analyze_with_ai(text, b['rol'], b['fac'], api_key)
                    if res:
                        res.update({'nombre': res.get('nombre', 'N/A'), 'facultad': b['fac'], 'cargo': b['rol']})
                        pdf = generate_pdf(res)
                        save_result(res, pdf, f_hash, f.name, b['id'])
                        processed += 1
                    else: errors += 1
                else: errors += 1
            
            progress.progress((processed + skipped + errors) / total_files)
            
            if (processed + skipped + errors) % 1 == 0:
                with table_ph.container():
                    df = get_dataframe()
                    if not df.empty:
                        st.dataframe(df[['timestamp', 'candidato', 'puntaje', 'recomendacion']], hide_index=True, use_container_width=True)

    status.success(f"✅ Finalizado. Nuevos: {processed} | Saltados: {skipped} | Errores: {errors}")
    time.sleep(2)
    st.rerun()

# ==============================================================================
# 6. INTERFAZ GRÁFICA
# ==============================================================================

with st.sidebar:
    st.header("🔧 Configuración")
    if 'api_input' not in st.session_state: st.session_state.api_input = ''
    
    if 'GOOGLE_API_KEY' in st.secrets:
        final_key = st.secrets['GOOGLE_API_KEY']
        st.success("🔑 API Key Corporativa")
    else:
        st.session_state.api_input = st.text_input("API Key Personal", type="password", value=st.session_state.api_input)
        final_key = st.session_state.api_input
    
    skip_dupes = st.checkbox("Omitir duplicados", value=True)
    if st.button("🗑️ Borrar Historial"):
        conn.cursor().execute("DELETE FROM cv_history"); conn.commit()
        st.rerun()

st.title("🚀 HR Intelligence Suite")
tab1, tab2, tab3 = st.tabs(["⚡ Centro de Carga", "📊 Dashboard", "📂 Repositorio"])

# LISTA CORRECTA DE FACULTADES
FACULTADES = [
    "Facultad de Ingeniería",
    "Facultad de Economía y Negocios",
    "Facultad de Ciencias de la Vida",
    "Facultad de Educación y Ciencias Sociales"
]

with tab1:
    col_a, col_b = st.columns(2)
    def render_batch(col, idx, border):
        with col:
            st.markdown(f"<div class='batch-card {border}'><span class='batch-title'>📂 Lote #{idx}</span></div>", unsafe_allow_html=True)
            files = st.file_uploader(f"Archivos {idx}", type=['pdf','docx'], key=f"f{idx}", accept_multiple_files=True, label_visibility="collapsed")
            c1, c2 = st.columns(2)
            fac = c1.selectbox("Facultad", FACULTADES, key=f"fac{idx}")
            rol = c2.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key=f"rol{idx}")
            if st.button(f"▶ Procesar Lote {idx}", key=f"b{idx}"):
                if final_key: execute_processing([{'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}], final_key, skip_dupes)
                else: st.error("Falta Key")
            return {'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}

    b1 = render_batch(col_a, 1, "b-blue")
    b2 = render_batch(col_b, 2, "b-green")
    b3 = render_batch(col_a, 3, "b-orange")
    b4 = render_batch(col_b, 4, "b-purple")
    
    st.markdown("---")
    if st.button("🚀 PROCESAR TODO", type="primary"):
        batches = [b for b in [b1, b2, b3, b4] if b['files']]
        if batches and final_key: execute_processing(batches, final_key, skip_dupes)
        elif not batches: st.warning("No hay archivos")
        else: st.error("Falta Key")

# --- DASHBOARD ---
with tab2:
    st.subheader("Dashboard Ejecutivo")
    df = get_dataframe()
    
    if not df.empty:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Candidatos", len(df))
        k2.metric("Promedio General", f"{df['puntaje'].mean():.2f}")
        k3.metric("Aptos", len(df[df['recomendacion'].str.contains("AVANZA", case=False)]))
        k4.metric("Última Actividad", df['timestamp'].iloc[0][:10])
        
        st.markdown("---")
        col_g1, col_g2 = st.columns([2, 1])
        
        with col_g1:
            st.markdown("#### 📉 Distribución de Puntajes por Facultad")
            if 'facultad' in df.columns and 'puntaje' in df.columns:
                fig_box = px.box(df, x="facultad", y="puntaje", color="facultad", points="all", template="plotly_dark", title="Análisis Comparativo")
                fig_box.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_box, use_container_width=True)
        
        with col_g2:
            st.markdown("#### 🎯 Decisiones")
            if 'recomendacion' in df.columns:
                df['estado_simple'] = df['recomendacion'].apply(lambda x: 'AVANZA' if 'AVANZA' in str(x).upper() else ('DESCARTADO' if 'NO' in str(x).upper() else 'DUDOSO'))
                fig_pie = px.pie(df, names="estado_simple", hole=0.4, template="plotly_dark", color="estado_simple", color_discrete_map={'AVANZA':'#2ecc71', 'DUDOSO':'#f1c40f', 'DESCARTADO':'#e74c3c'})
                fig_pie.update_layout(paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_pie, use_container_width=True)

        st.markdown("#### 🏆 Ranking")
        top_talent = df[df['puntaje'] >= 3.8].sort_values(by='puntaje', ascending=False).head(10)
        st.dataframe(top_talent[['candidato', 'puntaje', 'recomendacion', 'facultad', 'cargo']], column_config={"puntaje": st.column_config.ProgressColumn("Nota", min_value=0, max_value=5, format="%.2f")}, hide_index=True, use_container_width=True)
    else: st.info("Sin datos.")

with tab3:
    st.subheader("Gestión Documental")
    df = get_dataframe()
    if not df.empty:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            for i, r in df.iterrows():
                if r['pdf_blob']: zf.writestr(f"{re.sub(r'[^a-zA-Z0-9]', '_', str(r['candidato']))}.pdf", r['pdf_blob'])
        
        st.download_button("📦 Descargar Todos (ZIP)", zip_buf.getvalue(), "Reportes.zip", "application/zip", type="primary")
        st.dataframe(df.drop(columns=['pdf_blob', 'json_data', 'file_hash']), use_container_width=True)
        
        st.write("Descargas individuales:")
        for i, r in df.iterrows():
            with st.expander(f"{r['candidato']} ({r['puntaje']})"):
                st.write(r['recomendacion'])
                if r['pdf_blob']: st.download_button("Bajar PDF", r['pdf_blob'], f"informe.pdf", key=f"d{i}")
    else: st.info("Sin datos.")
