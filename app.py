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
from datetime import datetime
from fpdf import FPDF
import plotly.express as px
import plotly.graph_objects as go

# --- 1. CONFIGURACIÓN Y ESTILO CORPORATIVO ---
st.set_page_config(
    page_title="HR Intelligence Suite",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="expanded"
)

# CSS Profesional para UI Corporativa
st.markdown("""
    <style>
    /* Estilo General */
    .main { background-color: #f8f9fa; }
    h1, h2, h3 { font-family: 'Segoe UI', sans-serif; color: #2c3e50; }
    
    /* Tarjetas de Métricas */
    .metric-card {
        background-color: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        text-align: center;
        border-left: 5px solid #3498db;
    }
    
    /* Contenedores de Lotes */
    .batch-container {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 15px;
        margin-bottom: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    
    /* Botones */
    .stButton>button {
        width: 100%;
        border-radius: 5px;
        font-weight: bold;
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        background-color: #ffffff;
        border-radius: 5px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
    }
    .stTabs [aria-selected="true"] {
        background-color: #2c3e50;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

# --- 2. CONFIGURACIÓN DE RÚBRICA ---
RUBRICA = {
    "Docente": {
        "Formación": "35% | 5: PhD disciplina. 3-4: Magíster. 1-2: Diplomado.",
        "Experiencia": "30% | 5: >5 años + Innovación. 3-4: 3-5 años. 1-2: <3 años.",
        "Competencias": "20% | 5: Diseño Instruccional/Analíticas. 3-4: Metodologías Activas.",
        "Software": "15% | 5: IA/Autoría. 3-4: LMS avanzado."
    },
    "Investigador": {
        "Formación": "35% | 5: PhD Alta Productividad. 3-4: PhD + Postdoc.",
        "Experiencia": "30% | 5: >8 JCR Q1, Liderazgo. 3-4: 3 JCR Q1/Q2.",
        "Competencias": "20% | 5: Liderazgo Equipos/Transferencia. 3-4: SPSS/R.",
        "Software": "15% | 5: Big Data/Open Science. 3-4: Soft Estadístico."
    },
    "Gestión Académica": {
        "Formación": "35% | 5: PhD Política/Gestión. 3-4: Magíster Gestión.",
        "Experiencia": "30% | 5: Alta Dirección (Decano). 3-4: Dirección Carrera.",
        "Competencias": "20% | 5: Modelos Educativos/Políticas. 3-4: Acreditación.",
        "Software": "15% | 5: BI Estratégico/ISO. 3-4: ERP Académico."
    }
}

# --- 3. FUNCIONES CORE (LECTURA Y ANÁLISIS) ---

def read_file(file):
    """Lectura robusta de archivos"""
    try:
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
    except Exception:
        return ""

def analyze_gemini_safe(text, role, faculty, api_key):
    """Motor de IA con blindaje contra errores de formato JSON"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        crit = RUBRICA[role]
        
        prompt = f"""
        Rol: Headhunter Senior. Analiza CV para {role} en {faculty}.
        
        RÚBRICA:
        1. Formación: {crit['Formación']}
        2. Experiencia: {crit['Experiencia']}
        3. Competencias: {crit['Competencias']}
        4. Software: {crit['Software']}

        INSTRUCCIÓN TÉCNICA:
        Responde SOLO con un JSON válido. Sin markdown. Si hay comillas en el texto, usa simples.
        
        JSON:
        {{
            "nombre": "Nombre Apellido",
            "ajuste": "Alto/Medio/Bajo",
            "razon": "Frase corta",
            "rec": "Avanza/Dudoso/Descartado",
            "n_form": 0.0, "n_exp": 0.0, "n_comp": 0.0, "n_soft": 0.0,
            "comentarios": "Análisis ejecutivo."
        }}
        
        CV: {text[:14000]}
        """
        
        response = model.generate_content(prompt)
        raw = response.text
        
        # Limpieza JSON
        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start == -1 or end == 0: return None
        
        json_str = raw[start:end]
        
        try:
            data = json.loads(json_str)
        except:
            data = eval(json_str) # Fallback
            
        # Cálculos seguros
        def val(k): return float(data.get(k, 0))
        
        final = val('n_form')*0.35 + val('n_exp')*0.30 + val('n_comp')*0.20 + val('n_soft')*0.15
        
        return {
            **data,
            "final": round(final, 2),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
    except Exception as e:
        print(f"Error AI: {e}")
        return None

# --- 4. GENERACIÓN DE REPORTES PDF ---
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.set_text_color(44, 62, 80)
        self.cell(0, 10, 'Informe Confidencial de Evaluación', 0, 1, 'L')
        self.line(10, 20, 200, 20)
        self.ln(10)
    
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, f'HR Intelligence Suite - {datetime.now().year}', 0, 0, 'C')

def generate_pdf_bytes(data):
    pdf = PDFReport()
    pdf.add_page()
    
    # Función auxiliar para caracteres latinos
    def txt(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    
    # Encabezado Candidato
    pdf.set_font('Arial', 'B', 16)
    pdf.cell(0, 10, txt(data.get('nombre', 'Candidato')), 0, 1)
    
    pdf.set_font('Arial', '', 11)
    pdf.set_text_color(100)
    pdf.cell(0, 8, txt(f"{data['facultad']} | {data['cargo']}"), 0, 1)
    pdf.ln(5)
    
    # Caja de Resultado
    pdf.set_fill_color(236, 240, 241) # Gris muy claro
    pdf.rect(10, 50, 190, 30, 'F')
    
    pdf.set_xy(15, 55)
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(0)
    pdf.cell(90, 10, f"Puntaje Global: {data['final']} / 5.0")
    
    # Color condicional texto recomendación
    rec = data.get('rec', '')
    pdf.cell(0, 10, txt(f"Decisión: {rec}"), 0, 1)
    
    pdf.set_xy(15, 65)
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 10, txt(f"Nivel de Ajuste: {data.get('ajuste', '')} | {data.get('razon', '')}"), 0, 1)
    
    pdf.ln(25)
    
    # Tabla Detalle
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(52, 152, 219) # Azul corporativo
    pdf.set_text_color(255)
    pdf.cell(140, 8, "Dimensión Evaluada", 1, 0, 'L', True)
    pdf.cell(50, 8, "Puntaje", 1, 1, 'C', True)
    
    pdf.set_text_color(0)
    pdf.set_font('Arial', '', 10)
    
    items = [
        ("Formación Académica", data.get('n_form')),
        ("Experiencia Laboral", data.get('n_exp')),
        ("Competencias Técnicas", data.get('n_comp')),
        ("Software y Herramientas", data.get('n_soft'))
    ]
    
    for label, score in items:
        pdf.cell(140, 8, txt(label), 1)
        pdf.cell(50, 8, str(score), 1, 1, 'C')
        
    pdf.ln(10)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, "Análisis Cualitativo", 0, 1)
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 6, txt(data.get('comentarios', '')))
    
    return pdf.output(dest='S').encode('latin-1')

# --- 5. GESTIÓN DE ESTADO (MEMORIA) ---
if 'db' not in st.session_state:
    st.session_state.db = pd.DataFrame(columns=['Fecha', 'Candidato', 'Facultad', 'Cargo', 'Nota', 'Estado', 'Ajuste', 'Feedback', 'PDF'])

# --- 6. INTERFAZ PRINCIPAL ---

# Sidebar
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/9187/9187604.png", width=60)
    st.title("Admin Console")
    
    # Gestión de API Key
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("✅ Sistema Conectado")
    else:
        api_key = st.text_input("🔑 API Key", type="password")
        if not api_key: st.warning("Ingrese clave para operar")

    st.divider()
    st.info("La sesión mantiene los datos mientras el navegador esté abierto.")

# Título Principal
st.markdown("## 🏢 HR Intelligence Suite: Evaluación Académica")
st.markdown("Sistema centralizado de análisis curricular asistido por IA.")

# Pestañas
tab_load, tab_dash, tab_data, tab_docs = st.tabs([
    "⚡ Centro de Procesamiento (Multi-Lote)", 
    "📊 Dashboard Ejecutivo", 
    "🗃️ Base de Datos", 
    "📂 Repositorio PDF"
])

# --- TAB 1: CARGA MULTI-LOTE (LA INNOVACIÓN) ---
with tab_load:
    st.write("Configure hasta 4 lotes de carga simultánea para diferentes perfiles.")
    
    # Contenedor del Formulario
    with st.form("batch_process_form"):
        # Grid de 2x2 para los lotes
        col1, col2 = st.columns(2)
        
        batches_data = []
        
        # Función para renderizar un "Card" de lote
        def render_batch_slot(col, idx):
            with col:
                st.markdown(f"<div class='batch-container'><h4>📂 Lote de Carga #{idx}</h4>", unsafe_allow_html=True)
                files = st.file_uploader(f"Seleccionar CVs", type=['pdf','docx'], key=f"u_{idx}", accept_multiple_files=True)
                c_a, c_b = st.columns(2)
                fac = c_a.selectbox(f"Facultad", ["Ingeniería", "Economía", "Ciencias Vida", "Educación"], key=f"f_{idx}")
                rol = c_b.selectbox(f"Cargo", ["Docente", "Investigador", "Gestión Académica"], key=f"r_{idx}")
                st.markdown("</div>", unsafe_allow_html=True)
                return {"files": files, "fac": fac, "rol": rol}

        # Renderizar los 4 lotes
        b1 = render_batch_slot(col1, 1)
        b2 = render_batch_slot(col2, 2)
        b3 = render_batch_slot(col1, 3)
        b4 = render_batch_slot(col2, 4)
        
        # Botón Maestro
        st.markdown("---")
        submit_btn = st.form_submit_button("🚀 INICIAR PROCESAMIENTO MASIVO", type="primary")

    # Lógica de Procesamiento
    if submit_btn and api_key:
        active_batches = [b for b in [b1, b2, b3, b4] if b['files']]
        
        if not active_batches:
            st.warning("⚠️ Cargue archivos en al menos un lote para comenzar.")
        else:
            total_files = sum([len(b['files']) for b in active_batches])
            progress_bar = st.progress(0)
            status_text = st.empty()
            processed_count = 0
            
            for b_idx, batch in enumerate(active_batches):
                current_fac = batch['fac']
                current_rol = batch['rol']
                
                for f in batch['files']:
                    status_text.markdown(f"**Analizando:** {f.name} ({current_fac} - {current_rol})...")
                    
                    text = read_file(f)
                    if len(text) > 50:
                        res = analyze_gemini_safe(text, current_rol, current_fac, api_key)
                        
                        if res:
                            # Preparar registro
                            pdf_bytes = generate_pdf_bytes({**res, "facultad": current_fac, "cargo": current_rol})
                            
                            new_entry = {
                                "Fecha": res['timestamp'],
                                "Candidato": res['nombre'],
                                "Facultad": current_fac,
                                "Cargo": current_rol,
                                "Nota": res['final'],
                                "Estado": res['rec'],
                                "Ajuste": res['ajuste'],
                                "Feedback": res['comentarios'],
                                "n_form": res['n_form'],
                                "n_exp": res['n_exp'],
                                "n_comp": res['n_comp'],
                                "n_soft": res['n_soft'],
                                "PDF": pdf_bytes
                            }
                            # Guardar en sesión
                            st.session_state.db = pd.concat([st.session_state.db, pd.DataFrame([new_entry])], ignore_index=True)
                            processed_count += 1
                    
                    # Actualizar barra global
                    progress_bar.progress(processed_count / total_files)
            
            status_text.success(f"✅ Proceso finalizado. Se analizaron {processed_count} expedientes.")
            time.sleep(1)
            st.rerun()

# --- TAB 2: DASHBOARD (VISUALIZACIÓN) ---
with tab_dash:
    df = st.session_state.db
    if not df.empty:
        # KPIs Superiores
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Candidatos Procesados", len(df))
        k2.metric("Nota Promedio", f"{df['Nota'].mean():.2f}")
        k3.metric("Potenciales (Avanza)", len(df[df['Estado'] == 'Avanza']))
        top_fac = df['Facultad'].mode()[0] if not df.empty else "N/A"
        k4.metric("Facultad con más flujo", top_fac)
        
        st.divider()
        
        # Gráficos
        g1, g2 = st.columns([2, 1])
        
        with g1:
            st.subheader("Rendimiento por Facultad")
            fig = px.box(df, x="Facultad", y="Nota", color="Facultad", points="all", title="Distribución de Puntajes")
            st.plotly_chart(fig, use_container_width=True)
            
        with g2:
            st.subheader("Decisión Final")
            fig2 = px.pie(df, names="Estado", hole=0.4, color="Estado",
                          color_discrete_map={'Avanza':'#2ecc71', 'Dudoso':'#f1c40f', 'Descartado':'#e74c3c'})
            st.plotly_chart(fig2, use_container_width=True)
            
        # Tabla Ranking
        st.subheader("🏆 Top Talent (Nota > 4.0)")
        top_df = df[df['Nota'] >= 4.0].sort_values(by='Nota', ascending=False)
        st.dataframe(
            top_df[['Candidato', 'Cargo', 'Facultad', 'Nota', 'Estado']],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Nota": st.column_config.ProgressColumn("Puntaje", min_value=0, max_value=5, format="%.2f")
            }
        )
    else:
        st.info("📊 El dashboard se activará cuando procese los primeros CVs.")

# --- TAB 3: BASE DE DATOS ---
with tab_data:
    st.subheader("Registro Detallado")
    if not st.session_state.db.empty:
        # Filtros
        c_filter1, c_filter2 = st.columns(2)
        f_fac = c_filter1.multiselect("Filtrar Facultad", st.session_state.db['Facultad'].unique())
        f_est = c_filter2.multiselect("Filtrar Estado", st.session_state.db['Estado'].unique())
        
        df_show = st.session_state.db.copy()
        if f_fac: df_show = df_show[df_show['Facultad'].isin(f_fac)]
        if f_est: df_show = df_show[df_show['Estado'].isin(f_est)]
        
        st.dataframe(
            df_show.drop(columns=['PDF', 'n_form', 'n_exp', 'n_comp', 'n_soft']),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Sin datos registrados.")

# --- TAB 4: DESCARGAS ---
with tab_repo:
    st.subheader("Gestión Documental")
    df = st.session_state.db
    if not df.empty:
        # Descarga Masiva
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for idx, row in df.iterrows():
                clean_name = re.sub(r'[^a-zA-Z0-9]', '_', row['Candidato'])
                zf.writestr(f"{clean_name}_Informe.pdf", row['PDF'])
        
        st.download_button(
            label="📦 DESCARGAR TODO EL LOTE (ZIP)",
            data=zip_buffer.getvalue(),
            file_name=f"Informes_RRHH_{datetime.now().strftime('%Y%m%d')}.zip",
            mime="application/zip",
            type="primary"
        )
        
        st.markdown("---")
        st.write("Descargas Individuales:")
        
        # Grid para descargas individuales
        for idx, row in df.iterrows():
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1: st.write(f"📄 **{row['Candidato']}**")
            with c2: st.caption(f"{row['Cargo']} | {row['Facultad']}")
            with c3:
                st.download_button(
                    "⬇ PDF",
                    data=row['PDF'],
                    file_name=f"Informe_{row['Candidato']}.pdf",
                    key=f"dl_{idx}"
                )
            st.divider()
    else:
        st.info("Los informes generados aparecerán aquí.")
