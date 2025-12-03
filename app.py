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
# 1. CONFIGURACIÓN DEL ENTORNO Y ESTILOS (UI/UX)
# ==============================================================================

st.set_page_config(
    page_title="HR Intelligence Suite",
    layout="wide",
    page_icon="🎓",
    initial_sidebar_state="expanded"
)

# CSS Nativo y Limpio: Se adapta automáticamente al tema Claro/Oscuro del usuario.
# No forzamos colores de fondo para evitar problemas de contraste.
st.markdown("""
    <style>
    .main .block-container { padding-top: 1rem; }
    /* Ajuste de Tabs */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; border-radius: 5px; }
    /* Ajuste de Contenedores */
    div[data-testid="stExpander"] { border: 1px solid #4a4a4a; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# 2. CAPA DE PERSISTENCIA (SQLite)
# ==============================================================================

@st.cache_resource
def init_db():
    """
    Inicializa la conexión a la Base de Datos.
    Usamos cache_resource para mantener una única conexión abierta y evitar bloqueos.
    """
    conn = sqlite3.connect('cv_master_db.db', check_same_thread=False)
    c = conn.cursor()
    # Tabla Única Consolidada
    c.execute('''CREATE TABLE IF NOT EXISTS analisis (
                    file_hash TEXT PRIMARY KEY,
                    timestamp TEXT,
                    lote_nombre TEXT,
                    archivo_nombre TEXT,
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
    """Genera huella digital MD5 para detectar duplicados exactos."""
    return hashlib.md5(file_bytes).hexdigest()

def db_check_exists(file_hash):
    """Verifica si el hash ya existe en la BD."""
    c = conn.cursor()
    c.execute("SELECT candidato FROM analisis WHERE file_hash = ?", (file_hash,))
    return c.fetchone() is not None

def db_save_record(data_dict, pdf_bytes, file_hash, filename, lote_name):
    """Guarda o actualiza (UPSERT) un registro."""
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json_str = json.dumps(data_dict, ensure_ascii=False)
    
    # Manejo seguro de valores nulos
    puntaje = float(data_dict.get('puntaje_global', 0.0))
    
    c.execute('''INSERT OR REPLACE INTO analisis VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (file_hash, now, lote_name, filename, 
               data_dict.get('nombre', 'Desconocido'),
               data_dict.get('facultad', ''), 
               data_dict.get('cargo', ''),
               puntaje,
               data_dict.get('recomendacion', 'N/A'),
               data_dict.get('ajuste', 'N/A'),
               json_str, pdf_bytes))
    conn.commit()

def db_load_all():
    """Carga todo el historial para el Dashboard."""
    return pd.read_sql("SELECT * FROM analisis ORDER BY timestamp DESC", conn)

# ==============================================================================
# 3. MOTORES DE LECTURA Y ANÁLISIS (IA)
# ==============================================================================

def read_file_safe(file_obj):
    """Lee PDF o DOCX reiniciando el puntero para evitar errores de lectura vacía."""
    try:
        file_obj.seek(0)
        if file_obj.name.lower().endswith('.pdf'):
            reader = PdfReader(file_obj)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        elif file_obj.name.lower().endswith('.docx'):
            doc = Document(file_obj)
            return "\n".join([p.text for p in doc.paragraphs])
        return ""
    except Exception as e:
        return "" # Retorna vacío en caso de error para manejarlo arriba

def analyze_with_gemini(text, role, faculty, api_key, model_choice):
    """
    Motor de IA con Prompt Engineering Estricto.
    """
    if not api_key: return None
    genai.configure(api_key=api_key)
    
    # 1. Definición de Reglas de Negocio
    prompt = f"""
    Actúa como un Consultor Experto en Selección Académica. Analiza el siguiente CV.
    
    CONTEXTO:
    - Facultad: {faculty}
    - Cargo: {role}
    
    RÚBRICA DE PONDERACIÓN:
    1. Formación Académica (35%)
    2. Experiencia Laboral (30%)
    3. Competencias Técnicas (20%)
    4. Herramientas y Software (15%)
    
    REGLAS DE RECOMENDACIÓN (Estrictas):
    - Si Puntaje Global >= 3.75 -> "AVANZA"
    - Si Puntaje Global entre 3.00 y 3.74 -> "REQUIERE ANTECEDENTES"
    - Si Puntaje Global < 3.00 -> "NO RECOMENDADO"
    
    FORMATO DE SALIDA (JSON ÚNICAMENTE):
    Debes devolver un JSON válido con esta estructura exacta. Sin markdown, sin texto extra.
    {{
        "nombre": "Nombre y Apellido del Candidato",
        "ajuste": "ALTO / MEDIO / BAJO",
        "puntaje_global": 0.00,  // Float con 2 decimales
        "recomendacion": "AVANZA / REQUIERE ANTECEDENTES / NO RECOMENDADO",
        "conclusion_ejecutiva": "Resumen de 1 párrafo justificando la decisión.",
        "detalle_puntajes": {{
            "formacion": {{ "nota": 0, "ponderado": 0.00 }},
            "experiencia": {{ "nota": 0, "ponderado": 0.00 }},
            "competencias": {{ "nota": 0, "ponderado": 0.00 }},
            "software": {{ "nota": 0, "ponderado": 0.00 }}
        }},
        "analisis_cualitativo": {{
            "brechas": ["Punto 1", "Punto 2"],
            "riesgos": ["Punto 1", "Punto 2"],
            "fortalezas": ["Punto 1", "Punto 2"]
        }}
    }}
    
    DOCUMENTO A ANALIZAR:
    {text[:30000]}
    """
    
    try:
        model = genai.GenerativeModel(model_choice)
        response = model.generate_content(prompt)
        
        # Limpieza de respuesta (JSON Sanitization)
        raw = response.text
        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start == -1: return None
        
        return json.loads(raw[start:end])
    except Exception as e:
        print(f"Error IA: {e}")
        return None

# ==============================================================================
# 4. MOTOR DE REPORTES (PDF Pixel-Perfect)
# ==============================================================================

class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'INFORME DE AJUSTE CANDIDATO-CARGO', 0, 1, 'C')
        self.line(10, 20, 200, 20)
        self.ln(10)
    
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, f'Generado el {datetime.now().strftime("%d/%m/%Y")}', 0, 0, 'C')

def generate_pdf_report(data):
    pdf = PDFReport()
    pdf.add_page()
    
    # Helper para codificación Latin-1 (Tildes/Ñ)
    def txt(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    
    # 1. Encabezado de Datos
    pdf.set_font('Arial', 'B', 11); pdf.cell(30, 6, "Candidato:", 0, 0)
    pdf.set_font('Arial', '', 11); pdf.cell(0, 6, txt(data.get('nombre', 'N/A')), 0, 1)
    
    pdf.set_font('Arial', 'B', 11); pdf.cell(30, 6, "Cargo:", 0, 0)
    pdf.set_font('Arial', '', 11); pdf.cell(0, 6, txt(f"{data.get('cargo')} - {data.get('facultad')}"), 0, 1)
    pdf.ln(8)
    
    # 2. Sección A: Ejecutiva
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("A. CONCLUSIÓN EJECUTIVA"), 1, 1, 'L', True)
    pdf.ln(2)
    
    pdf.set_font('Arial', '', 10)
    resumen = f"Nivel de Ajuste: {data.get('ajuste')}. Puntaje Global: {data.get('puntaje_global', 0):.2f} / 5.00.\n{data.get('conclusion_ejecutiva')}"
    pdf.multi_cell(0, 5, txt(resumen))
    pdf.ln(5)
    
    # Caja de Recomendación (Semáforo)
    rec = data.get('recomendacion', '').upper()
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
    pdf.cell(0, 10, txt(rec), 1, 1, 'C', True)
    
    pdf.set_text_color(0) # Reset
    pdf.ln(10)
    
    # 3. Sección B: Tabla
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("B. TABLA RESUMEN DE CALIFICACIÓN"), 1, 1, 'L', True)
    
    # Encabezados
    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(50, 50, 50); pdf.set_text_color(255)
    pdf.cell(80, 8, "Variable", 1, 0, 'L', True)
    pdf.cell(30, 8, "Ponderación", 1, 0, 'C', True)
    pdf.cell(30, 8, "Nota (0-5)", 1, 0, 'C', True)
    pdf.cell(50, 8, "Puntaje Ponderado", 1, 1, 'C', True)
    pdf.set_text_color(0); pdf.set_font('Arial', '', 9)
    
    det = data.get('detalle_puntajes', {})
    dims = [
        ("Formación Académica", "35%", det.get('formacion', {})),
        ("Experiencia Laboral", "30%", det.get('experiencia', {})),
        ("Competencias Técnicas", "20%", det.get('competencias', {})),
        ("Herramientas y Software", "15%", det.get('software', {}))
    ]
    
    for n, p, v in dims:
        pdf.ln(8)
        pdf.cell(80, 8, txt(n), 1)
        pdf.cell(30, 8, p, 1, 0, 'C')
        pdf.cell(30, 8, str(v.get('nota', 0)), 1, 0, 'C')
        pdf.cell(50, 8, f"{v.get('ponderado', 0):.2f}", 1, 0, 'C')
        
    pdf.ln(8)
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font('Arial', 'B', 9)
    pdf.cell(140, 8, "TOTAL PONDERADO", 1, 0, 'R', True)
    pdf.cell(50, 8, f"{data.get('puntaje_global', 0):.2f}", 1, 1, 'C', True)
    pdf.ln(10)
    
    # 4. Sección C: Cualitativo
    pdf.set_font('Arial', 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, txt("C. ANÁLISIS CUALITATIVO"), 1, 1, 'L', True)
    pdf.ln(2)
    
    cual = data.get('analisis_cualitativo', {})
    for k, v in cual.items():
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 6, txt(k.upper().replace("_", " ")), 0, 1)
        pdf.set_font('Arial', '', 10)
        items = v if isinstance(v, list) else [str(v)]
        for i in items:
            pdf.multi_cell(0, 5, txt(f"- {i}"))
        pdf.ln(2)
        
    return bytes(pdf.output())

# ==============================================================================
# 5. LÓGICA DE INTERFAZ Y ORQUESTACIÓN
# ==============================================================================

# --- SIDEBAR CONFIG ---
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # API Key con fallback a secrets
    api_key = st.text_input("Google API Key", type="password")
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("Licencia Corporativa Detectada")
        
    st.divider()
    
    # Selección de Modelo (Control del Usuario)
    st.subheader("Modelo IA")
    model_choice = st.selectbox(
        "Versión del Modelo:",
        ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.0-pro"],
        index=0,
        help="Use Flash para velocidad, Pro para razonamiento complejo."
    )
    
    # Control de Rate Limit
    req_delay = st.slider("Pausa entre análisis (seg)", 2, 20, 5, help="Aumentar si recibe errores de cuota.")
    
    st.divider()
    skip_dupes = st.checkbox("Omitir Duplicados", value=True)
    
    if st.button("🗑️ Resetear Base de Datos"):
        conn.cursor().execute("DELETE FROM analisis")
        conn.commit()
        st.warning("Historial eliminado.")
        time.sleep(1)
        st.rerun()

# --- HEADER PRINCIPAL ---
st.title("🚀 HR Intelligence Suite")
st.markdown("Plataforma de Análisis Curricular Masivo")

# --- TABS DE NAVEGACIÓN ---
tab_input, tab_dashboard, tab_data, tab_repo = st.tabs([
    "📥 Centro de Carga", 
    "📊 Dashboard Ejecutivo", 
    "🗃️ Base de Datos en Vivo", 
    "📂 Repositorio PDF"
])

# Variables Globales de Interfaz
FACULTADES = [
    "Facultad de Ingeniería", 
    "Facultad de Economía y Negocios", 
    "Facultad de Ciencias de la Vida", 
    "Facultad de Educación y Ciencias Sociales"
]
CARGOS = ["Docente", "Investigador", "Gestión Académica"]

# ==============================================================================
# PESTAÑA 1: CENTRO DE CARGA (Input)
# ==============================================================================
with tab_input:
    st.info("Configure los lotes de carga. Puede procesar individualmente o ejecutar un barrido masivo.")
    
    c1, c2 = st.columns(2)
    batches_config = [] # Para almacenar la configuración y ejecutar masivo
    
    def render_batch_uploader(col, idx):
        with col:
            with st.container(border=True):
                st.subheader(f"📂 Lote #{idx}")
                files = st.file_uploader(f"Documentos Lote {idx}", accept_multiple_files=True, key=f"f{idx}")
                fac = st.selectbox("Facultad", FACULTADES, key=f"fac{idx}")
                rol = st.selectbox("Cargo", CARGOS, key=f"rol{idx}")
                
                # Guardar en lista para proceso masivo
                if files:
                    batches_config.append({'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol})
                
                # Botón de Proceso Individual
                if st.button(f"▶ Procesar Lote {idx}", key=f"btn{idx}"):
                    if not files or not api_key:
                        st.error("Faltan archivos o API Key")
                    else:
                        run_processing([{'id': f"Lote {idx}", 'files': files, 'fac': fac, 'rol': rol}])

    # Lógica de Ejecución Centralizada
    def run_processing(batch_list):
        total_docs = sum(len(b['files']) for b in batch_list)
        
        # Alerta de Tiempo
        est_min = (total_docs * (req_delay + 3)) // 60
        st.toast(f"Iniciando análisis de {total_docs} documentos. Estimado: {est_min} min.")
        
        progress = st.progress(0, "Preparando...")
        status = st.empty()
        
        processed, skipped, errors = 0, 0, 0
        current_step = 0
        
        for b in batch_list:
            for f in b['files']:
                current_step += 1
                status.text(f"Procesando {current_step}/{total_docs}: {f.name} ({b['id']})")
                
                # 1. Hash & Check
                f.seek(0); f_bytes = f.read(); f_hash = get_file_hash(f_bytes)
                
                if skip_dupes and db_check_exists(f_hash):
                    skipped += 1
                else:
                    # 2. Leer & Analizar
                    f.seek(0)
                    txt = read_file_safe(f)
                    if len(txt) > 50:
                        res = analyze_with_gemini(txt, b['rol'], b['fac'], api_key, model_choice)
                        if res:
                            # 3. Guardar
                            res.update({'facultad': b['fac'], 'cargo': b['rol']})
                            pdf = generate_pdf_report(res)
                            db_save_record(res, pdf, f_hash, f.name, b['id'])
                            processed += 1
                            time.sleep(req_delay) # Rate Limit
                        else:
                            errors += 1
                    else:
                        errors += 1
                
                progress.progress(current_step / total_docs)
        
        status.success(f"Finalizado: {processed} Procesados | {skipped} Duplicados | {errors} Errores")
        time.sleep(2)
        st.rerun()

    # Renderizado de la UI de Lotes
    render_batch_uploader(c1, 1); render_batch_uploader(c2, 2)
    render_batch_uploader(c1, 3); render_batch_uploader(c2, 4)
    
    st.markdown("---")
    if st.button("🚀 PROCESAR TODOS LOS LOTES ACTIVOS", type="primary", use_container_width=True):
        if not api_key: st.error("Falta API Key")
        elif not batches_config: st.warning("No hay archivos cargados en ningún lote")
        else: run_processing(batches_config)

# ==============================================================================
# PESTAÑA 2: DASHBOARD EJECUTIVO
# ==============================================================================
with tab_dashboard:
    st.header("📊 Tablero de Control")
    df = db_load_all()
    
    if df.empty:
        st.info("No hay datos históricos. Procese archivos para visualizar métricas.")
    else:
        # KPIs
        k1, k2, k3 = st.columns(3)
        k1.metric("Total Procesado", len(df))
        k2.metric("Puntaje Promedio", f"{df['puntaje'].mean():.2f}")
        k3.metric("Tasa de Aprobación", f"{(len(df[df['recomendacion']=='AVANZA']) / len(df) * 100):.1f}%")
        
        st.divider()
        
        # Gráficos
        g1, g2 = st.columns(2)
        with g1:
            st.subheader("Distribución por Facultad")
            fig_box = px.box(df, x="facultad", y="puntaje", color="facultad", points="all")
            st.plotly_chart(fig_box, use_container_width=True)
            
        with g2:
            st.subheader("Decisiones")
            fig_pie = px.pie(df, names="recomendacion", hole=0.4, color="recomendacion",
                             color_discrete_map={"AVANZA":"#2ecc71", "NO RECOMENDADO":"#e74c3c", "REQUIERE ANTECEDENTES":"#f1c40f"})
            st.plotly_chart(fig_pie, use_container_width=True)
            
        st.subheader("🏆 Ranking Top Talent")
        st.dataframe(
            df[df['puntaje'] >= 3.75].sort_values(by="puntaje", ascending=False)[['candidato', 'puntaje', 'facultad', 'cargo']],
            use_container_width=True,
            hide_index=True
        )

# ==============================================================================
# PESTAÑA 3: BASE DE DATOS (DATA GRID)
# ==============================================================================
with tab_data:
    st.header("🗃️ Registro Detallado")
    st.caption("Visualización en tiempo real de los registros almacenados.")
    
    df_grid = db_load_all()
    if df_grid.empty:
        st.warning("Base de datos vacía.")
    else:
        # Configuración de Columnas para UX
        st.dataframe(
            df_grid[['timestamp', 'lote_nombre', 'candidato', 'facultad', 'cargo', 'puntaje', 'recomendacion']],
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Fecha/Hora", format="DD/MM HH:mm:ss"),
                "puntaje": st.column_config.ProgressColumn("Puntaje", min_value=0, max_value=5, format="%.2f"),
                "lote_nombre": "Lote Origen"
            },
            use_container_width=True,
            height=600
        )
        
        st.download_button(
            "💾 Descargar Tabla Completa (Excel)",
            data=df_grid.drop(columns=['pdf_blob', 'raw_json']).to_csv(index=False).encode('utf-8'),
            file_name="Reporte_Consolidado.csv",
            mime="text/csv"
        )

# ==============================================================================
# PESTAÑA 4: REPOSITORIO PDF
# ==============================================================================
with tab_repo:
    st.header("📂 Gestión Documental")
    df_repo = db_load_all()
    
    if df_repo.empty:
        st.info("Sin informes generados.")
    else:
        # ZIP Generator
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for idx, row in df_repo.iterrows():
                if row['pdf_blob']:
                    fname = f"{re.sub(r'[^a-zA-Z0-9]', '_', str(row['candidato']))}_{row['lote_nombre']}.pdf"
                    zf.writestr(fname, row['pdf_blob'])
        
        st.download_button(
            "📦 Descargar Todos los Informes (ZIP)",
            data=zip_buffer.getvalue(),
            file_name="Informes_Completos.zip",
            mime="application/zip",
            type="primary"
        )
        
        st.divider()
        st.write("Descarga Individual:")
        
        for idx, row in df_repo.iterrows():
            with st.expander(f"{row['candidato']} - {row['puntaje']:.2f}"):
                c_info, c_btn = st.columns([3, 1])
                c_info.write(f"**Cargo:** {row['cargo']} | **Estado:** {row['recomendacion']}")
                if row['pdf_blob']:
                    c_btn.download_button("Descargar PDF", row['pdf_blob'], f"Informe_{row['candidato']}.pdf", key=f"pdf_{idx}")
