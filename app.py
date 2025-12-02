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

# --- 1. CONFIGURACIÓN Y ESTILO CORPORATIVO (DARK MODE FRIENDLY) ---
st.set_page_config(
    page_title="HR Intelligence Suite",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="expanded"
)

# CSS PROFESIONAL
st.markdown("""
    <style>
    /* Ajuste General para Modo Oscuro */
    .main { background-color: #0E1117; }
    
    h1, h2, h3 {
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        color: #FAFAFA !important;
        font-weight: 600;
    }
    
    p, label { color: #E0E0E0 !important; }

    /* TARJETAS DE LOTES */
    .batch-card {
        background-color: #262730;
        border-radius: 10px;
        padding: 15px;
        margin-bottom: 10px;
        border: 1px solid #363945;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
    }
    
    /* Bordes de color */
    .border-blue { border-top: 4px solid #3498db; }
    .border-green { border-top: 4px solid #2ecc71; }
    .border-orange { border-top: 4px solid #e67e22; }
    .border-purple { border-top: 4px solid #9b59b6; }

    .batch-card h4 {
        margin-top: 0;
        font-size: 1.1rem;
        color: #ffffff !important;
        border-bottom: 1px solid #444;
        padding-bottom: 10px;
        margin-bottom: 15px;
    }

    /* BOTONES */
    .stButton>button {
        width: 100%;
        border-radius: 6px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    
    /* TABS */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        background-color: #262730;
        border-radius: 5px;
        border: 1px solid #363945;
        color: #cccccc;
    }
    .stTabs [aria-selected="true"] {
        background-color: #3498db !important;
        color: white !important;
        border-color: #3498db;
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

# --- 3. FUNCIONES CORE ---

def read_file(file):
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
        Responde SOLO con un JSON válido. Sin markdown.
        
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
        
        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start == -1 or end == 0: return None
        json_str = raw[start:end]
        
        try:
            data = json.loads(json_str)
        except:
            data = eval(json_str) 
            
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
    def txt(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    
    pdf.set_font('Arial', 'B', 16)
    pdf.cell(0, 10, txt(data.get('nombre', 'Candidato')), 0, 1)
    
    pdf.set_font('Arial', '', 11)
    pdf.set_text_color(100)
    pdf.cell(0, 8, txt(f"{data['facultad']} | {data['cargo']}"), 0, 1)
    pdf.ln(5)
    
    pdf.set_fill_color(236, 240, 241) 
    pdf.rect(10, 50, 190, 30, 'F')
    
    pdf.set_xy(15, 55)
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(0)
    pdf.cell(90, 10, f"Puntaje Global: {data['final']} / 5.0")
    
    rec = data.get('rec', '')
    pdf.cell(0, 10, txt(f"Decisión: {rec}"), 0, 1)
    
    pdf.set_xy(15, 65)
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 10, txt(f"Nivel de Ajuste: {data.get('ajuste', '')} | {data.get('razon', '')}"), 0, 1)
    
    pdf.ln(25)
    
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(52, 152, 219) 
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

# --- 5. LÓGICA DE PROCESAMIENTO REUTILIZABLE ---
def process_cvs(file_list, faculty, role, api_key):
    """Procesa una lista de archivos y devuelve el número de éxitos"""
    count = 0
    for f in file_list:
        text = read_file(f)
        if len(text) > 50:
            res = analyze_gemini_safe(text, role, faculty, api_key)
            if res:
                pdf_bytes = generate_pdf_bytes({**res, "facultad": faculty, "cargo": role})
                new_entry = {
                    "Fecha": res['timestamp'],
                    "Candidato": res['nombre'],
                    "Facultad": faculty,
                    "Cargo": role,
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
                st.session_state.db = pd.concat([st.session_state.db, pd.DataFrame([new_entry])], ignore_index=True)
                count += 1
    return count

# --- 6. GESTIÓN DE ESTADO ---
if 'db' not in st.session_state:
    st.session_state.db = pd.DataFrame(columns=['Fecha', 'Candidato', 'Facultad', 'Cargo', 'Nota', 'Estado', 'Ajuste', 'Feedback', 'PDF'])

# --- 7. INTERFAZ PRINCIPAL ---

# Sidebar
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/9187/9187604.png", width=50)
    st.title("Admin Console")
    
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("✅ Sistema Conectado")
    else:
        api_key = st.text_input("🔑 API Key", type="password")
    
    st.divider()
    st.info("Sistema optimizado para grandes volúmenes de datos.")

# Título
st.markdown("## 🏢 HR Intelligence Suite: Evaluación Académica")

# Pestañas
tab_load, tab_dash, tab_data, tab_repo = st.tabs([
    "⚡ Procesamiento (Multi-Lote)", 
    "📊 Dashboard Ejecutivo", 
    "🗃️ Base de Datos", 
    "📂 Repositorio PDF"
])

# --- TAB 1: CARGA MULTI-LOTE MEJORADA (ESTRUCTURA HÍBRIDA) ---
with tab_load:
    st.markdown("### Configuración de Carga Simultánea")
    st.markdown("Utilice los botones individuales para procesar lotes específicos, o el botón maestro para procesar todo.")
    
    # --- FILA 1 ---
    col1, col2 = st.columns(2)
    
    # LOTE 1 (AZUL)
    with col1:
        st.markdown('<div class="batch-card border-blue"><h4>📂 Lote #1</h4></div>', unsafe_allow_html=True)
        files1 = st.file_uploader("Archivos Lote 1", type=['pdf','docx'], key="u_1", accept_multiple_files=True, label_visibility="collapsed")
        c1a, c1b = st.columns(2)
        fac1 = c1a.selectbox("Facultad", ["Ingeniería", "Economía", "Ciencias Vida", "Educación"], key="f_1")
        rol1 = c1b.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key="r_1")
        if st.button("Procesar Lote 1", key="btn_1"):
            if files1 and api_key:
                with st.spinner("Analizando Lote 1..."):
                    n = process_cvs(files1, fac1, rol1, api_key)
                    st.success(f"✅ Lote 1: {n} procesados.")
                    time.sleep(1)
                    st.rerun()

    # LOTE 2 (VERDE)
    with col2:
        st.markdown('<div class="batch-card border-green"><h4>📂 Lote #2</h4></div>', unsafe_allow_html=True)
        files2 = st.file_uploader("Archivos Lote 2", type=['pdf','docx'], key="u_2", accept_multiple_files=True, label_visibility="collapsed")
        c2a, c2b = st.columns(2)
        fac2 = c2a.selectbox("Facultad", ["Ingeniería", "Economía", "Ciencias Vida", "Educación"], key="f_2")
        rol2 = c2b.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key="r_2")
        if st.button("Procesar Lote 2", key="btn_2"):
            if files2 and api_key:
                with st.spinner("Analizando Lote 2..."):
                    n = process_cvs(files2, fac2, rol2, api_key)
                    st.success(f"✅ Lote 2: {n} procesados.")
                    time.sleep(1)
                    st.rerun()

    # --- FILA 2 ---
    col3, col4 = st.columns(2)

    # LOTE 3 (NARANJA)
    with col3:
        st.markdown('<div class="batch-card border-orange"><h4>📂 Lote #3</h4></div>', unsafe_allow_html=True)
        files3 = st.file_uploader("Archivos Lote 3", type=['pdf','docx'], key="u_3", accept_multiple_files=True, label_visibility="collapsed")
        c3a, c3b = st.columns(2)
        fac3 = c3a.selectbox("Facultad", ["Ingeniería", "Economía", "Ciencias Vida", "Educación"], key="f_3")
        rol3 = c3b.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key="r_3")
        if st.button("Procesar Lote 3", key="btn_3"):
            if files3 and api_key:
                with st.spinner("Analizando Lote 3..."):
                    n = process_cvs(files3, fac3, rol3, api_key)
                    st.success(f"✅ Lote 3: {n} procesados.")
                    time.sleep(1)
                    st.rerun()

    # LOTE 4 (PURPURA)
    with col4:
        st.markdown('<div class="batch-card border-purple"><h4>📂 Lote #4</h4></div>', unsafe_allow_html=True)
        files4 = st.file_uploader("Archivos Lote 4", type=['pdf','docx'], key="u_4", accept_multiple_files=True, label_visibility="collapsed")
        c4a, c4b = st.columns(2)
        fac4 = c4a.selectbox("Facultad", ["Ingeniería", "Economía", "Ciencias Vida", "Educación"], key="f_4")
        rol4 = c4b.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key="r_4")
        if st.button("Procesar Lote 4", key="btn_4"):
            if files4 and api_key:
                with st.spinner("Analizando Lote 4..."):
                    n = process_cvs(files4, fac4, rol4, api_key)
                    st.success(f"✅ Lote 4: {n} procesados.")
                    time.sleep(1)
                    st.rerun()

    # --- BOTÓN MAESTRO ---
    st.markdown("---")
    st.markdown("### Acciones Globales")
    
    if st.button("🚀 PROCESAMIENTO MASIVO (TODOS LOS LOTES ACTIVOS)", type="primary"):
        if not api_key:
            st.error("🔑 Ingrese API Key")
        else:
            total_processed = 0
            bar = st.progress(0)
            status = st.empty()
            
            # Recopilar lotes activos
            batches = [
                (files1, fac1, rol1),
                (files2, fac2, rol2),
                (files3, fac3, rol3),
                (files4, fac4, rol4)
            ]
            
            # Filtrar vacíos
            active_batches = [b for b in batches if b[0]]
            
            if not active_batches:
                st.warning("⚠️ No hay archivos cargados en ningún lote.")
            else:
                total_files_global = sum([len(b[0]) for b in active_batches])
                current_count = 0
                
                for files, fac, rol in active_batches:
                    for f in files:
                        status.markdown(f"**Procesando:** {f.name}...")
                        text = read_file(f)
                        if len(text) > 50:
                            res = analyze_gemini_safe(text, rol, fac, api_key)
                            if res:
                                pdf_bytes = generate_pdf_bytes({**res, "facultad": fac, "cargo": rol})
                                new_entry = {
                                    "Fecha": res['timestamp'],
                                    "Candidato": res['nombre'],
                                    "Facultad": fac,
                                    "Cargo": rol,
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
                                st.session_state.db = pd.concat([st.session_state.db, pd.DataFrame([new_entry])], ignore_index=True)
                                total_processed += 1
                        
                        current_count += 1
                        bar.progress(current_count / total_files_global)
                
                status.success(f"✅ Proceso Global Finalizado: {total_processed} CVs analizados.")
                time.sleep(2)
                st.rerun()

# --- TAB 2: DASHBOARD ---
with tab_dash:
    df = st.session_state.db
    if not df.empty:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Procesados", len(df))
        k2.metric("Promedio Global", f"{df['Nota'].mean():.2f}")
        k3.metric("Aptos", len(df[df['Estado'] == 'Avanza']))
        top_fac = df['Facultad'].mode()[0] if not df.empty else "-"
        k4.metric("Facultad Top", top_fac)
        
        st.markdown("---")
        
        g1, g2 = st.columns([2, 1])
        with g1:
            st.subheader("Distribución de Puntajes")
            fig = px.box(df, x="Facultad", y="Nota", color="Facultad", points="all", template="plotly_dark")
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
            
        with g2:
            st.subheader("Decisión Final")
            fig2 = px.pie(df, names="Estado", hole=0.4, color="Estado", template="plotly_dark",
                          color_discrete_map={'Avanza':'#2ecc71', 'Dudoso':'#f1c40f', 'Descartado':'#e74c3c'})
            fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig2, use_container_width=True)
            
        st.subheader("🏆 Top Talent")
        top_df = df[df['Nota'] >= 3.8].sort_values(by='Nota', ascending=False)
        st.dataframe(
            top_df[['Candidato', 'Cargo', 'Facultad', 'Nota', 'Estado']],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Nota": st.column_config.ProgressColumn("Puntaje", min_value=0, max_value=5, format="%.2f")
            }
        )
    else:
        st.info("El dashboard se actualizará automáticamente al procesar CVs.")

# --- TAB 3: BASE DE DATOS ---
with tab_data:
    st.subheader("Base de Datos Histórica")
    if not st.session_state.db.empty:
        c_filter1, c_filter2 = st.columns(2)
        f_fac = c_filter1.multiselect("Filtrar Facultad", st.session_state.db['Facultad'].unique())
        f_est = c_filter2.multiselect("Filtrar Estado", st.session_state.db['Estado'].unique())
        
        df_show = st.session_state.db.copy()
        if f_fac: df_show = df_show[df_show['Facultad'].isin(f_fac)]
        if f_est: df_show = df_show[df_show['Estado'].isin(f_est)]
        
        st.dataframe(df_show.drop(columns=['PDF', 'n_form', 'n_exp', 'n_comp', 'n_soft']), use_container_width=True, hide_index=True)
    else:
        st.info("Sin registros.")

# --- TAB 4: DESCARGAS ---
with tab_repo:
    st.subheader("Repositorio Documental")
    df = st.session_state.db
    if not df.empty:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for idx, row in df.iterrows():
                clean_name = re.sub(r'[^a-zA-Z0-9]', '_', row['Candidato'])
                zf.writestr(f"{clean_name}_Informe.pdf", row['PDF'])
        
        st.download_button("📦 DESCARGAR TODO (ZIP)", data=zip_buffer.getvalue(), file_name=f"Informes_{datetime.now().strftime('%Y%m%d')}.zip", type="primary")
        st.markdown("---")
        
        for idx, row in df.iterrows():
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1: st.write(f"📄 **{row['Candidato']}**")
            with c2: st.caption(f"{row['Cargo']}")
            with c3:
                st.download_button("⬇ PDF", data=row['PDF'], file_name=f"Informe_{row['Candidato']}.pdf", key=f"dl_{idx}")
            st.divider()
    else:
        st.info("Los informes generados aparecerán aquí.")
