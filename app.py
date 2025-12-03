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

# Aumentar ancho del layout
st.markdown("""
    <style>
    /* 1. Corrección de Contraste y Tema Oscuro Corporativo */
    .stApp {
        background-color: #0E1117;
    }
    
    /* Textos */
    h1, h2, h3, h4, h5, h6 {
        color: #FAFAFA !important;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }
    p, label, span, div {
        color: #E0E0E0;
    }
    
    /* 2. Tarjetas de Lotes (Batch Cards) */
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
    
    /* Bordes de color para identificación */
    .b-blue { border-top: 4px solid #3498db; }
    .b-green { border-top: 4px solid #2ecc71; }
    .b-orange { border-top: 4px solid #e67e22; }
    .b-purple { border-top: 4px solid #9b59b6; }

    /* 3. Botones y Inputs */
    .stButton>button {
        width: 100%;
        border-radius: 4px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        transition: 0.2s;
    }
    .stButton>button:hover {
        transform: translateY(-1px);
        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
    }
    
    /* 4. Tablas */
    .stDataFrame {
        border: 1px solid #444;
    }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. GESTIÓN DE BASE DE DATOS (PERSISTENCIA Y CONTROL DE DUPLICADOS)
# ==============================================================================

def init_db():
    """Inicializa la BD SQLite para historial persistente."""
    conn = sqlite3.connect('cv_database.db', check_same_thread=False)
    c = conn.cursor()
    # Tabla optimizada con Hash para evitar duplicados
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
    """Genera huella digital única del archivo."""
    file_obj.seek(0) # IMPORTANTE: Rebobinar antes de leer
    data = file_obj.read()
    file_obj.seek(0) # Rebobinar después de leer
    return hashlib.md5(data).hexdigest()

def check_history(file_hash):
    """Verifica si el archivo ya existe en la BD."""
    c = conn.cursor()
    c.execute("SELECT candidato, timestamp, lote_id, puntaje, recomendacion FROM cv_history WHERE file_hash = ?", (file_hash,))
    return c.fetchone()

def save_result(data_dict, pdf_bytes, file_hash, filename, lote_id):
    """Guarda o actualiza el registro en la BD."""
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
    """Obtiene el historial completo para el dashboard."""
    return pd.read_sql("SELECT * FROM cv_history ORDER BY timestamp DESC", conn)

# ==============================================================================
# 3. MOTORES DE LECTURA Y ANÁLISIS IA
# ==============================================================================

def read_file_content(file):
    """Lee PDF o Word de forma robusta."""
    try:
        file.seek(0) # CRÍTICO: Siempre al inicio
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
    except Exception as e:
        return f"Error leyendo archivo: {e}"

def analyze_with_ai(text, role, faculty, api_key):
    """Motor de IA con auto-discovery de modelos y limpieza JSON."""
    if not api_key: return None
    
    genai.configure(api_key=api_key)
    
    # 1. Definición de Rúbrica según cargo
    base_rubrica = {
        "Formación": "35%", "Experiencia": "30%", "Competencias": "20%", "Software": "15%"
    }
    
    # 2. Prompt de Ingeniería Estricta
    prompt = f"""
    Actúa como Experto en Selección Académica. Evalúa el CV para Cargo: "{role}" en Facultad: "{faculty}".
    
    RÚBRICA: {json.dumps(base_rubrica)}
    
    INSTRUCCIONES CRÍTICAS DE SALIDA:
    1. Responde SOLO con un JSON válido.
    2. "puntaje_global" debe ser float con 2 decimales (Escala 1.00 a 5.00).
    3. "recomendacion" debe ser EXACTAMENTE una de: "NO RECOMENDADO", "REQUIERE ANTECEDENTES", "AVANZA".
    
    ESTRUCTURA JSON:
    {{
        "nombre": "Nombre Apellido",
        "ajuste": "ALTO/MEDIO/BAJO",
        "puntaje_global": 0.00,
        "recomendacion": "ESTADO",
        "conclusion_ejecutiva": "Resumen ejecutivo profesional.",
        "detalle": {{
            "formacion": {{ "nota": 0, "ponderado": 0.00 }},
            "experiencia": {{ "nota": 0, "ponderado": 0.00 }},
            "competencias": {{ "nota": 0, "ponderado": 0.00 }},
            "software": {{ "nota": 0, "ponderado": 0.00 }}
        }},
        "cualitativo": {{
            "brechas": ["punto 1", "punto 2"],
            "riesgos": ["punto 1", "punto 2"],
            "fortalezas": ["punto 1", "punto 2"]
        }}
    }}
    
    CV: {text[:25000]}
    """
    
    # 3. Selección de Modelo (Fallback automático)
    models_to_try = ['gemini-2.0-flash-exp', 'gemini-1.5-flash', 'gemini-1.5-pro']
    response = None
    
    for m in models_to_try:
        try:
            model = genai.GenerativeModel(m)
            response = model.generate_content(prompt)
            break
        except: continue
        
    if not response: return None

    # 4. Limpieza y Parseo
    try:
        raw = response.text
        # Encontrar el primer { y el último }
        start, end = raw.find('{'), raw.rfind('}') + 1
        if start == -1: return None
        
        json_clean = raw[start:end]
        return json.loads(json_clean)
    except:
        return None

# ==============================================================================
# 4. GENERADOR DE PDF CORPORATIVO
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
        self.cell(0, 10, f'HR Intelligence Suite - Generado: {datetime.now().strftime("%d/%m/%Y %H:%M")}', 0, 0, 'C')

def generate_pdf(data):
    pdf = PDFReport()
    pdf.add_page()
    
    # Helper para encoding Latin-1
    def txt(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    
    # 1. Datos Generales
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(30, 6, "Cargo:", 0, 0)
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, txt(f"{data['cargo']} - {data['facultad']}"), 0, 1)
    
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(30, 6, "Candidato:", 0, 0)
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, txt(data['nombre']), 0, 1)
    pdf.ln(8)
    
    # 2. Conclusión Ejecutiva
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("A. CONCLUSIÓN EJECUTIVA"), 1, 1, 'L', True)
    pdf.ln(2)
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 5, txt(f"Nivel de ajuste detectado: {data['ajuste']}. Puntaje Global: {data['puntaje_global']} / 5.00."))
    pdf.ln(2)
    pdf.multi_cell(0, 5, txt(data['conclusion_ejecutiva']))
    pdf.ln(5)
    
    # Caja de Recomendación
    rec = data['recomendacion'].upper()
    if "NO" in rec:
        pdf.set_fill_color(255, 200, 200) # Rojo
        pdf.set_text_color(150, 0, 0)
    elif "AVANZA" in rec:
        pdf.set_fill_color(200, 255, 200) # Verde
        pdf.set_text_color(0, 100, 0)
    else:
        pdf.set_fill_color(255, 255, 200) # Amarillo
        pdf.set_text_color(100, 100, 0)
        
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 12, txt(rec), 1, 1, 'C', True)
    pdf.set_text_color(0) # Reset
    pdf.ln(8)
    
    # 3. Tabla de Puntajes
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("B. TABLA RESUMEN DE CALIFICACIÓN"), 1, 1, 'L', True)
    
    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(50, 50, 50)
    pdf.set_text_color(255)
    pdf.cell(80, 8, "Dimensión", 1, 0, 'L', True)
    pdf.cell(30, 8, "Ponderación", 1, 0, 'C', True)
    pdf.cell(30, 8, "Nota (0-5)", 1, 0, 'C', True)
    pdf.cell(50, 8, "Puntaje Ponderado", 1, 1, 'C', True)
    
    pdf.set_text_color(0)
    pdf.set_font('Arial', '', 9)
    
    det = data['detalle']
    dims = [
        ("Formación Profesional", "35%", det.get('formacion', {})),
        ("Experiencia Laboral", "30%", det.get('experiencia', {})),
        ("Competencias Técnicas", "20%", det.get('competencias', {})),
        ("Herramientas y Software", "15%", det.get('software', {}))
    ]
    
    for name, pond, vals in dims:
        pdf.ln(8)
        pdf.cell(80, 8, txt(name), 1)
        pdf.cell(30, 8, pond, 1, 0, 'C')
        pdf.cell(30, 8, str(vals.get('nota', 0)), 1, 0, 'C')
        pdf.cell(50, 8, f"{vals.get('ponderado', 0):.2f}", 1, 0, 'C')
    
    pdf.ln(8)
    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(140, 8, "TOTAL PONDERADO", 1, 0, 'R', True)
    pdf.cell(50, 8, f"{data['puntaje_global']} / 5.00", 1, 1, 'C', True)
    
    # 4. Cualitativo
    pdf.ln(10)
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("C. COMENTARIOS FINALES (Brechas, Riesgos, Fortalezas)"), 1, 1, 'L', True)
    pdf.ln(2)
    
    cual = data['cualitativo']
    
    def print_list(title, items):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 6, txt(title), 0, 1)
        pdf.set_font('Arial', '', 10)
        for i in items:
            pdf.cell(5, 5, "-", 0, 0)
            pdf.multi_cell(0, 5, txt(str(i)))
        pdf.ln(2)

    print_list("1. Brechas Identificadas:", cual.get('brechas', []))
    print_list("2. Riesgos Potenciales:", cual.get('riesgos', []))
    print_list("3. Fortalezas y Diferenciadores:", cual.get('fortalezas', []))
    
    return bytes(pdf.output())

# ==============================================================================
# 5. LÓGICA DE PROCESAMIENTO CENTRAL
# ==============================================================================

def execute_processing(batches_to_process, api_key, skip_duplicates=True):
    """
    Ejecuta el análisis de los lotes seleccionados.
    batches_to_process: Lista de diccionarios {files, fac, rol, id}
    """
    total_files = sum([len(b['files']) for b in batches_to_process])
    if total_files == 0:
        st.warning("⚠️ No hay archivos seleccionados.")
        return

    # 1. Estimación de Tiempo (Aviso previo)
    est_segundos = total_files * 4 # Promedio 4s por archivo
    est_mins = est_segundos // 60
    
    st.info(f"⏱️ Se procesarán {total_files} documentos. Tiempo estimado: {est_mins} min {est_segundos%60} seg.")
    
    # Contenedores para Feedback en tiempo real
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Placeholder para tabla viva
    table_placeholder = st.empty()
    
    processed_count = 0
    skipped_count = 0
    error_count = 0
    
    for batch in batches_to_process:
        lote_id = batch['id']
        fac = batch['fac']
        rol = batch['rol']
        
        for file in batch['files']:
            status_text.text(f"Analizando: {file.name} ({lote_id})...")
            
            # 1. Verificar Duplicidad
            file_hash = get_file_hash(file)
            exists = check_history(file_hash)
            
            if exists and skip_duplicates:
                skipped_count += 1
                status_text.text(f"⏭️ Saltado (Duplicado): {file.name}")
            else:
                # 2. Procesar
                text = read_file_content(file)
                if len(text) > 50:
                    res = analyze_with_ai(text, rol, fac, api_key)
                    
                    if res:
                        # Completar datos faltantes para PDF
                        res['nombre'] = res.get('nombre', 'Candidato Desconocido')
                        res['facultad'] = fac
                        res['cargo'] = rol
                        
                        pdf_bytes = generate_pdf(res)
                        save_result(res, pdf_bytes, file_hash, file.name, lote_id)
                        processed_count += 1
                    else:
                        error_count += 1
                else:
                    error_count += 1
            
            # Actualizar Progreso Global
            current_total = processed_count + skipped_count + error_count
            progress_bar.progress(min(current_total / total_files, 1.0))
            
            # Actualizar Tabla en Vivo (Cada 3 archivos para no saturar)
            if current_total % 1 == 0:
                df_live = get_dataframe()
                with table_placeholder.container():
                    st.dataframe(
                        df_live[['timestamp', 'lote_id', 'candidato', 'puntaje', 'recomendacion']],
                        hide_index=True,
                        use_container_width=True
                    )
    
    status_text.success(f"✅ Proceso Finalizado. Nuevos: {processed_count} | Omitidos: {skipped_count} | Errores: {error_count}")
    time.sleep(2)
    st.rerun()

# ==============================================================================
# 6. INTERFAZ GRÁFICA (LAYOUT)
# ==============================================================================

# Sidebar
with st.sidebar:
    st.header("🔧 Configuración")
    
    # API Key persistente en Session State si se ingresa manual
    if 'api_key_input' not in st.session_state:
        st.session_state.api_key_input = ''
    
    # Prioridad: Secrets > Input Manual
    final_api_key = None
    if 'GOOGLE_API_KEY' in st.secrets:
        final_api_key = st.secrets['GOOGLE_API_KEY']
        st.success("🔑 API Key Corporativa Activa")
    else:
        st.session_state.api_key_input = st.text_input("Ingrese API Key Personal", type="password", value=st.session_state.api_key_input)
        final_api_key = st.session_state.api_key_input
        
    st.divider()
    
    st.subheader("⚙️ Opciones de Proceso")
    skip_dupes = st.checkbox("Omitir duplicados", value=True, help="Si el archivo ya fue analizado antes (mismo contenido), no lo procesa de nuevo.")
    
    st.divider()
    if st.button("🗑️ Borrar Historial de Base de Datos"):
        c = conn.cursor()
        c.execute("DELETE FROM cv_history")
        conn.commit()
        st.warning("Base de datos reiniciada.")
        st.rerun()

# Título
st.title("🚀 HR Intelligence Suite")
st.markdown("Sistema de Evaluación Curricular Masiva con Detección de Duplicados e IA Generativa.")

# Pestañas Principales
tab1, tab2, tab3 = st.tabs(["⚡ Centro de Carga", "📊 Dashboard en Vivo", "📂 Repositorio"])

# --- TAB 1: CENTRO DE CARGA (4 LOTES + GLOBAL) ---
with tab1:
    col_a, col_b = st.columns(2)
    
    # Helper para dibujar tarjetas de lote
    def draw_batch_card(col, idx, border_class):
        with col:
            st.markdown(f"""
            <div class="batch-card {border_class}">
                <span class="batch-title">📂 Lote de Carga #{idx}</span>
            </div>
            """, unsafe_allow_html=True)
            
            # Widgets
            files = st.file_uploader(f"Archivos Lote {idx}", type=['pdf','docx'], key=f"files_{idx}", accept_multiple_files=True, label_visibility="collapsed")
            c1, c2 = st.columns(2)
            fac = c1.selectbox("Facultad", ["Ingeniería", "Economía y Negocios", "Salud", "Educación"], key=f"fac_{idx}")
            rol = c2.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key=f"rol_{idx}")
            
            # Botón individual
            if st.button(f"▶ Procesar Solo Lote {idx}", key=f"btn_{idx}"):
                if not final_api_key:
                    st.error("Falta API Key")
                else:
                    batch_data = [{'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}]
                    execute_processing(batch_data, final_api_key, skip_dupes)
            
            return {'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}

    # Renderizar Lotes
    b1 = draw_batch_card(col_a, 1, "b-blue")
    b2 = draw_batch_card(col_b, 2, "b-green")
    b3 = draw_batch_card(col_a, 3, "b-orange")
    b4 = draw_batch_card(col_b, 4, "b-purple")
    
    st.markdown("---")
    
    # Botón Global
    if st.button("🚀 PROCESAR TODOS LOS LOTES CON ARCHIVOS", type="primary", use_container_width=True):
        if not final_api_key:
            st.error("Falta API Key")
        else:
            # Filtrar solo los lotes que tienen archivos
            all_batches = [b for b in [b1, b2, b3, b4] if b['files']]
            if not all_batches:
                st.warning("No hay archivos cargados en ningún lote.")
            else:
                execute_processing(all_batches, final_api_key, skip_dupes)

# --- TAB 2: DASHBOARD (TIEMPO REAL) ---
with tab2:
    st.subheader("Resultados de Evaluación")
    
    df = get_dataframe()
    
    if not df.empty:
        # Métricas
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Procesado", len(df))
        m2.metric("Promedio Puntaje", f"{df['puntaje'].mean():.2f}")
        m3.metric("Aptos (Avanza)", len(df[df['recomendacion'].str.contains("AVANZA", na=False)]))
        m4.metric("Última Carga", df['timestamp'].iloc[0][:10])
        
        # Tabla Principal
        st.dataframe(
            df[['timestamp', 'lote_id', 'candidato', 'facultad', 'cargo', 'puntaje', 'recomendacion']],
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Hora Carga", format="HH:mm:ss"),
                "puntaje": st.column_config.ProgressColumn("Puntaje", min_value=0, max_value=5, format="%.2f"),
                "recomendacion": st.column_config.TextColumn("Estado")
            },
            hide_index=True,
            use_container_width=True,
            height=600
        )
    else:
        st.info("No hay datos históricos. Procese archivos para comenzar.")

# --- TAB 3: REPOSITORIO Y DESCARGAS ---
with tab3:
    st.subheader("Gestión Documental")
    df = get_dataframe()
    
    if not df.empty:
        # Descarga Masiva ZIP
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            for idx, row in df.iterrows():
                if row['pdf_blob']:
                    # Limpiar nombre de archivo
                    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', str(row['candidato']))
                    zf.writestr(f"{clean_name}.pdf", row['pdf_blob'])
        
        col_d1, col_d2 = st.columns([1, 2])
        col_d1.download_button("📦 Descargar ZIP (Todos los Informes)", zip_buf.getvalue(), "Informes_Completos.zip", "application/zip", type="primary")
        
        col_d2.download_button(
            "💾 Descargar Tabla Excel",
            data=df.drop(columns=['pdf_blob', 'json_data', 'file_hash']).to_csv(index=False).encode('utf-8'),
            file_name="Reporte_Data.csv",
            mime="text/csv"
        )
        
        st.divider()
        st.write("Descargas Individuales:")
        
        for idx, row in df.iterrows():
            with st.expander(f"📄 {row['candidato']} - {row['cargo']} ({row['puntaje']})"):
                c1, c2 = st.columns([3, 1])
                c1.write(f"**Recomendación:** {row['recomendacion']}")
                c1.write(f"**Fecha:** {row['timestamp']}")
                if row['pdf_blob']:
                    c2.download_button("Descargar PDF", row['pdf_blob'], f"Informe_{row['candidato']}.pdf", key=f"dl_{idx}")
    else:
        st.info("Sin documentos disponibles.")
