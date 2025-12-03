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
import concurrent.futures
from datetime import datetime
from fpdf import FPDF
import plotly.express as px

# --- 1. CONFIGURACIÓN E INICIALIZACIÓN ---
st.set_page_config(
    page_title="HR Intelligence Suite Pro",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="collapsed"
)

# Aumentar límite de carga visual (aunque el límite real depende de config.toml del servidor)
# st.set_option('server.maxUploadSize', 500) 

# --- 2. GESTIÓN DE BASE DE DATOS (HISTORIAL Y DUPLICADOS) ---
def init_db():
    conn = sqlite3.connect('cv_history.db', check_same_thread=False)
    c = conn.cursor()
    # Tabla principal
    c.execute('''CREATE TABLE IF NOT EXISTS evaluations (
                    file_hash TEXT PRIMARY KEY,
                    fecha_carga TEXT,
                    lote_id TEXT,
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

def get_file_hash(file_bytes):
    """Genera un hash único para el contenido del archivo."""
    return hashlib.md5(file_bytes).hexdigest()

def check_if_exists(file_hash):
    """Verifica si el archivo ya fue procesado."""
    c = conn.cursor()
    c.execute("SELECT candidato, fecha_carga, lote_id FROM evaluations WHERE file_hash = ?", (file_hash,))
    return c.fetchone()

def save_to_db(data, file_hash, pdf_bytes, lote_id):
    c = conn.cursor()
    json_str = json.dumps(data)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Insertar o Reemplazar (Upsert)
    c.execute('''INSERT OR REPLACE INTO evaluations 
                 (file_hash, fecha_carga, lote_id, candidato, facultad, cargo, puntaje, recomendacion, ajuste, json_data, pdf_blob)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (file_hash, now, lote_id, data['nombre'], data['facultad'], data['cargo'], 
               data['puntaje_global'], data['recomendacion'], data['ajuste'], json_str, pdf_bytes))
    conn.commit()

def load_history():
    return pd.read_sql("SELECT * FROM evaluations ORDER BY fecha_carga DESC", conn)

# --- 3. ESTILOS CSS (UI MEJORADA) ---
st.markdown("""
    <style>
    .main { background-color: #f4f6f9; color: #333; }
    h1, h2, h3 { color: #2c3e50 !important; font-family: 'Segoe UI', sans-serif; }
    
    /* Tablas */
    .stDataFrame { font-size: 0.85rem; }
    
    /* Cards de Lotes */
    .batch-card {
        background-color: white;
        border-radius: 8px;
        padding: 15px;
        margin-bottom: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        border-left: 5px solid #bdc3c7;
    }
    .batch-active { border-left-color: #3498db; }
    
    /* Botones */
    .stButton>button { width: 100%; border-radius: 4px; font-weight: 600; }
    
    /* Status */
    .status-ok { color: #27ae60; font-weight: bold; }
    .status-warn { color: #e67e22; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- 4. RÚBRICA ---
RUBRICA = {
    "Docente": {
        "Formación": "35%", "Experiencia": "30%", "Competencias": "20%", "Software": "15%"
    },
    "Investigador": {
        "Formación": "35%", "Experiencia": "30%", "Competencias": "20%", "Software": "15%"
    },
    "Gestión Académica": {
        "Formación": "35%", "Experiencia": "30%", "Competencias": "20%", "Software": "15%"
    }
}

# --- 5. LECTURA DE ARCHIVOS ---
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

# --- 6. MOTOR DE IA (GEMINI) ---
def get_best_model(api_key):
    genai.configure(api_key=api_key)
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # Prioridad: 1.5 Flash (Rápido) -> 2.0 -> Pro
        for m in models: 
            if 'flash' in m.lower() and '1.5' in m: return m
        return 'gemini-1.5-pro' # Fallback seguro
    except: return 'gemini-pro'

def analyze_cv_ai(text, role, faculty, api_key):
    model_name = get_best_model(api_key)
    genai.configure(api_key=api_key)
    
    prompt = f"""
    Actúa como Experto en Selección Académica. Analiza el CV para el cargo: {role} en la Facultad: {faculty}.
    
    RÚBRICA DE PONDERACIÓN:
    {json.dumps(RUBRICA.get(role, RUBRICA['Docente']))}

    INSTRUCCIONES DE SALIDA (JSON ESTRICTO):
    Debes extraer y evaluar generando este JSON exacto. Los campos de texto deben ser profesionales.
    
    {{
        "nombre": "Nombre Apellido",
        "ajuste": "ALTO / MEDIO / BAJO",
        "puntaje_global": 0.00, (Con dos decimales, escala 1.0 a 5.0)
        "recomendacion": "AVANZA / REQUIERE ANTECEDENTES / NO RECOMENDADO",
        "conclusion_ejecutiva": "Párrafo resumen de 4-5 líneas justificando el ajuste y la nota.",
        "detalle_puntajes": {{
            "formacion": {{ "nota": 0, "ponderado": 0.00 }},
            "experiencia": {{ "nota": 0, "ponderado": 0.00 }},
            "competencias": {{ "nota": 0, "ponderado": 0.00 }},
            "software": {{ "nota": 0, "ponderado": 0.00 }}
        }},
        "analisis_cualitativo": {{
            "brechas": ["Brecha 1", "Brecha 2"],
            "riesgos": ["Riesgo 1", "Riesgo 2"],
            "fortalezas": ["Fortaleza 1", "Fortaleza 2"]
        }}
    }}
    
    CV TEXTO:
    {text[:20000]}
    """
    
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        # Limpieza JSON
        raw = response.text
        start, end = raw.find('{'), raw.rfind('}') + 1
        if start == -1: return None
        return json.loads(raw[start:end])
    except Exception as e:
        print(f"Error AI: {e}")
        return None

# --- 7. GENERADOR PDF (FORMATO SOLICITADO) ---
class PDFReportPro(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 16)
        self.cell(0, 10, 'INFORME DE AJUSTE CANDIDATO-CARGO', 0, 1, 'L')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Generado: {datetime.now().strftime("%d/%m/%Y")} | HR Suite', 0, 0, 'C')

def generate_pdf(data):
    pdf = PDFReportPro()
    pdf.add_page()
    
    def safe_txt(t): return str(t).encode('latin-1', 'replace').decode('latin-1')
    
    # 1. Encabezado Datos
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(25, 6, "Cargo:", 0, 0)
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, safe_txt(f"{data['cargo']} - {data['facultad']}"), 0, 1)
    
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(25, 6, "Candidato:", 0, 0)
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, safe_txt(data['nombre']), 0, 1)
    pdf.ln(8)
    
    # A. CONCLUSIÓN EJECUTIVA
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 8, "A. CONCLUSIÓN EJECUTIVA", 0, 1)
    pdf.set_font('Arial', '', 10)
    
    # Texto dinámico
    intro = f"El candidato presenta un nivel de ajuste {data['ajuste']} para el cargo evaluado. Obtuvo un puntaje global estimado de {data['puntaje_global']} / 5.00."
    pdf.multi_cell(0, 5, safe_txt(intro))
    pdf.ln(3)
    
    # CAJA DE RECOMENDACIÓN (Color según estado)
    rec = data['recomendacion'].upper()
    if "NO" in rec:
        pdf.set_fill_color(250, 220, 220) # Rojo claro
        pdf.set_text_color(180, 0, 0)
    elif "AVANZA" in rec:
        pdf.set_fill_color(220, 250, 220) # Verde claro
        pdf.set_text_color(0, 100, 0)
    else:
        pdf.set_fill_color(255, 240, 200) # Amarillo
        pdf.set_text_color(150, 100, 0)
        
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(0, 12, safe_txt(rec), 1, 1, 'C', fill=True)
    
    pdf.set_text_color(0) # Reset color
    pdf.set_font('Arial', '', 10)
    pdf.ln(3)
    pdf.multi_cell(0, 5, safe_txt(data['conclusion_ejecutiva']))
    pdf.ln(8)
    
    # B. TABLA RESUMEN
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 8, "B. TABLA RESUMEN DE CALIFICACIÓN", 0, 1)
    
    # Header Tabla
    pdf.set_fill_color(50, 60, 70)
    pdf.set_text_color(255)
    pdf.set_font('Arial', 'B', 9)
    pdf.cell(80, 8, "Dimensión", 1, 0, 'L', True)
    pdf.cell(30, 8, "Ponderación", 1, 0, 'C', True)
    pdf.cell(30, 8, "Puntaje (0-5)", 1, 0, 'C', True)
    pdf.cell(50, 8, "Puntaje Ponderado", 1, 1, 'C', True)
    
    # Filas
    pdf.set_text_color(0)
    pdf.set_font('Arial', '', 9)
    det = data['detalle_puntajes']
    dims = [
        ("Formación Profesional", "35%", det['formacion']),
        ("Experiencia Laboral", "30%", det['experiencia']),
        ("Competencias Técnicas", "20%", det['competencias']),
        ("Herramientas y Software", "15%", det['software']),
    ]
    
    for nombre, pond, vals in dims:
        pdf.cell(80, 7, safe_txt(nombre), 1)
        pdf.cell(30, 7, pond, 1, 0, 'C')
        pdf.cell(30, 7, f"{vals['nota']}", 1, 0, 'C')
        pdf.cell(50, 7, f"{vals['ponderado']:.2f}", 1, 1, 'C')
        
    # Total
    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(140, 8, "TOTAL PONDERADO", 1, 0, 'R', True)
    pdf.cell(50, 8, f"{data['puntaje_global']} / 5.00", 1, 1, 'C', True)
    pdf.ln(8)
    
    # C. COMENTARIOS FINALES
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 8, "C. ANÁLISIS CUALITATIVO (Brechas, Riesgos, Fortalezas)", 0, 1)
    
    def print_section(title, items):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 6, safe_txt(title), 0, 1)
        pdf.set_font('Arial', '', 10)
        for item in items:
            pdf.cell(5, 5, chr(149), 0, 0) # Bullet
            pdf.multi_cell(0, 5, safe_txt(item))
        pdf.ln(3)

    cual = data['analisis_cualitativo']
    print_section(f"1. Principales Brechas para {data['cargo']}", cual.get('brechas', []))
    print_section("2. Riesgos para el desempeño", cual.get('riesgos', []))
    print_section("3. Fortalezas y diferenciadores", cual.get('fortalezas', []))

    return bytes(pdf.output())

# --- 8. LÓGICA DE PROCESAMIENTO (CONCURRENTE Y CONTROLADA) ---
def process_single_file(file_obj, fac, rol, api_key, lote_name):
    """Procesa un solo archivo. Retorna el resultado o None si hay error."""
    try:
        # Leer bytes para hash
        file_bytes = file_obj.getvalue()
        f_hash = get_file_hash(file_bytes)
        
        # Verificar duplicado en BD
        exists = check_if_exists(f_hash)
        
        # Si existe, NO reprocesamos (ahorramos dinero y tiempo), devolvemos el histórico
        if exists:
            # Recuperar data completa es complejo aquí sin hacer otra query, 
            # pero para efectos de velocidad, asumimos que si existe, retornamos un flag especial
            # Ojo: Para regenerar la tabla en vivo, podríamos volver a analizar si el usuario forzó
            # pero por defecto saltamos.
            return {"status": "duplicate", "name": file_obj.name, "hash": f_hash}
        
        # Lectura de texto
        text = read_file_content(file_obj)
        if len(text) < 50:
            return {"status": "error", "msg": "Archivo vacío", "name": file_obj.name}
            
        # Análisis IA
        ai_res = analyze_cv_ai(text, rol, fac, api_key)
        if not ai_res:
            return {"status": "error", "msg": "IA falló", "name": file_obj.name}
            
        # Añadir metadatos que faltan en el JSON de la IA
        ai_res['facultad'] = fac
        ai_res['cargo'] = rol
        
        # Generar PDF
        pdf_bytes = generate_pdf(ai_res)
        
        # Guardar en BD
        save_to_db(ai_res, f_hash, pdf_bytes, lote_name)
        
        return {"status": "success", "name": file_obj.name, "data": ai_res}
        
    except Exception as e:
        return {"status": "error", "msg": str(e), "name": file_obj.name}

# --- 9. INTERFAZ GRÁFICA ---

# Sidebar
with st.sidebar:
    st.header("Configuración")
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("API Key cargada")
    else:
        api_key = st.text_input("API Key", type="password")
        
    st.divider()
    if st.button("Borrar Historial Completo"):
        c = conn.cursor()
        c.execute("DELETE FROM evaluations")
        conn.commit()
        st.warning("Base de datos reiniciada.")
        st.rerun()

# Título
st.title("🚀 HR Intelligence Suite: Procesamiento Masivo")

# Tabs
tab_load, tab_dash, tab_history = st.tabs(["⚡ Carga y Análisis", "📊 Resultados en Vivo", "🗃️ Historial Completo"])

# --- TAB 1: CARGA ---
with tab_load:
    st.info("Configure hasta 4 lotes. El sistema detecta duplicados automáticamente.")
    
    # Definición de Lotes (usando Session State para persistencia UI)
    col1, col2 = st.columns(2)
    
    def render_batch(col, idx):
        with col:
            st.markdown(f"<div class='batch-card'><h5>📂 Lote #{idx}</h5>", unsafe_allow_html=True)
            files = st.file_uploader(f"CVs Lote {idx}", type=['pdf','docx'], key=f"u_{idx}", accept_multiple_files=True)
            
            c_a, c_b = st.columns(2)
            fac = c_a.selectbox("Facultad", ["Economía y Negocios", "Ingeniería", "Salud", "Educación"], key=f"f_{idx}")
            rol = c_b.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key=f"r_{idx}")
            
            st.markdown("</div>", unsafe_allow_html=True)
            return {"id": f"Lote {idx}", "files": files, "fac": fac, "rol": rol}

    b1 = render_batch(col1, 1)
    b2 = render_batch(col2, 2)
    b3 = render_batch(col1, 3)
    b4 = render_batch(col2, 4)
    
    batches = [b1, b2, b3, b4]
    
    # Lógica de Pre-Cálculo
    all_files_count = sum([len(b['files']) for b in batches if b['files']])
    
    if all_files_count > 0:
        st.divider()
        st.subheader("Resumen de Carga")
        
        # Estimación
        est_time = all_files_count * 4 # 4 segundos prom por archivo
        mins = est_time // 60
        secs = est_time % 60
        
        c_info1, c_info2 = st.columns(2)
        c_info1.metric("Total Archivos Detectados", all_files_count)
        c_info2.metric("Tiempo Estimado", f"{mins} min {secs} seg")
        
        st.warning(f"⚠️ Se procesarán {all_files_count} archivos. Los duplicados se omitirán automáticamente.")
        
        if st.button("🚀 INICIAR PROCESAMIENTO INTELIGENTE", type="primary"):
            if not api_key:
                st.error("Falta API Key")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                results_container = st.container()
                
                # Lista plana de tareas
                tasks = []
                for b in batches:
                    if b['files']:
                        for f in b['files']:
                            tasks.append({
                                "file": f,
                                "fac": b['fac'],
                                "rol": b['rol'],
                                "lote": b['id']
                            })
                
                # Procesamiento Concurrente (ThreadPool)
                # Max workers = 5 para no saturar API rate limit de Google
                processed_ok = 0
                duplicates = 0
                errors = 0
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    # Mapear futuros
                    future_to_file = {
                        executor.submit(process_single_file, t['file'], t['fac'], t['rol'], api_key, t['lote']): t['file'].name
                        for t in tasks
                    }
                    
                    completed = 0
                    for future in concurrent.futures.as_completed(future_to_file):
                        fname = future_to_file[future]
                        try:
                            res = future.result()
                            if res['status'] == 'success':
                                processed_ok += 1
                                status_text.write(f"✅ {fname}: Procesado correctamente.")
                            elif res['status'] == 'duplicate':
                                duplicates += 1
                                status_text.write(f"🔄 {fname}: Ya existe en BD. Omitido.")
                            else:
                                errors += 1
                                status_text.write(f"❌ {fname}: {res.get('msg')}")
                        except Exception as exc:
                            errors += 1
                            print(f"{fname} generó excepción: {exc}")
                        
                        completed += 1
                        progress_bar.progress(completed / len(tasks))
                
                st.success(f"Fin del proceso. Nuevos: {processed_ok} | Duplicados: {duplicates} | Errores: {errors}")
                time.sleep(2)
                st.rerun()

# --- TAB 2 Y 3: VISUALIZACIÓN ---
# Cargar datos frescos de BD
df = load_history()

def show_data_table(dataframe):
    if dataframe.empty:
        st.info("No hay datos.")
        return

    # Configuración avanzada de columnas
    st.dataframe(
        dataframe,
        column_config={
            "pdf_blob": st.column_config.Column("PDF", disabled=True), # Ocultar blob visualmente feo
            "puntaje": st.column_config.ProgressColumn("Puntaje", min_value=0, max_value=5, format="%.2f"),
            "fecha_carga": st.column_config.DatetimeColumn("Fecha", format="DD/MM HH:mm"),
            "json_data": None, # Ocultar raw json
            "file_hash": None
        },
        use_container_width=True,
        hide_index=True,
        height=500
    )

with tab_dash:
    st.subheader("Resultados Recientes (Tiempo Real)")
    # Filtramos los de hoy o ultima sesión
    show_data_table(df)
    
    if not df.empty:
        # Descarga Masiva ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for idx, row in df.iterrows():
                if row['pdf_blob']:
                    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', row['candidato'])
                    zf.writestr(f"{clean_name}.pdf", row['pdf_blob'])
        
        st.download_button("📦 Descargar Todos los Informes (ZIP)", zip_buffer.getvalue(), "Informes_Lote.zip", "application/zip", type="primary")

with tab_history:
    st.subheader("Base de Datos Histórica Global")
    
    # Filtros
    c1, c2 = st.columns(2)
    filtro_lote = c1.multiselect("Filtrar por Lote", df['lote_id'].unique() if not df.empty else [])
    filtro_estado = c2.multiselect("Filtrar por Recomendación", df['recomendacion'].unique() if not df.empty else [])
    
    df_show = df.copy()
    if filtro_lote: df_show = df_show[df_show['lote_id'].isin(filtro_lote)]
    if filtro_estado: df_show = df_show[df_show['recomendacion'].isin(filtro_estado)]
    
    show_data_table(df_show)
