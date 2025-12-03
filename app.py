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

# --- 1. CONFIGURACIÓN E INICIALIZACIÓN ---
st.set_page_config(
    page_title="HR Intelligence Suite",
    layout="wide",
    page_icon="🏢",
    initial_sidebar_state="expanded"
)

# Inicializar estado
if 'db' not in st.session_state:
    st.session_state.db = pd.DataFrame(columns=[
        'Fecha', 'Candidato', 'Facultad', 'Cargo', 'Nota', 
        'Estado', 'Ajuste', 'Feedback', 'PDF', 
        'n_form', 'n_exp', 'n_comp', 'n_soft'
    ])

# CSS PROFESIONAL
st.markdown("""
    <style>
    .main { background-color: #0E1117; }
    h1, h2, h3 { color: #FAFAFA !important; font-family: 'Helvetica Neue', sans-serif; font-weight: 600; }
    p, label, li { color: #E0E0E0 !important; }
    .batch-card { background-color: #262730; border-radius: 8px; padding: 15px; margin-bottom: 15px; border: 1px solid #363945; }
    .batch-header { font-size: 1.1rem; font-weight: bold; color: white; padding-bottom: 10px; margin-bottom: 15px; border-bottom: 1px solid #444; }
    .border-1 { border-top: 4px solid #3498db; }
    .border-2 { border-top: 4px solid #2ecc71; }
    .border-3 { border-top: 4px solid #e67e22; }
    .border-4 { border-top: 4px solid #9b59b6; }
    .stButton>button { width: 100%; border-radius: 6px; font-weight: 600; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { background-color: #262730; border: 1px solid #363945; }
    .stTabs [aria-selected="true"] { background-color: #3498db !important; color: white !important; }
    </style>
""", unsafe_allow_html=True)

# --- 2. RÚBRICA ---
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

# --- 3. FUNCIONES DE LECTURA ---
def read_file(file):
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

# --- 4. MOTOR DE IA (SOLUCIÓN DEFINITIVA AUTODESCUBRIMIENTO) ---
def get_best_model(api_key):
    """
    Consulta a Google qué modelos están disponibles y elige el mejor.
    Evita errores 404 por nombres incorrectos.
    """
    genai.configure(api_key=api_key)
    try:
        # 1. Obtener lista real de modelos disponibles para esta API Key
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        # 2. Estrategia de selección (Prioridad: Flash > Pro > Cualquiera)
        # Buscamos coincidencias parciales porque los nombres cambian (ej: models/gemini-1.5-flash-001)
        for m in available_models:
            if 'flash' in m.lower() and '1.5' in m: return m
        for m in available_models:
            if 'pro' in m.lower() and '1.5' in m: return m
        for m in available_models:
            if 'gemini-pro' in m.lower(): return m
            
        # 3. Si encontramos algo, devolvemos el primero
        if available_models:
            return available_models[0]
        
        return 'gemini-pro' # Fallback final
        
    except Exception as e:
        # Si falla el listado, usamos el clásico seguro
        return 'gemini-pro'

def analyze_gemini_safe(text, role, faculty, api_key):
    # Obtener el modelo correcto dinámicamente
    model_name = get_best_model(api_key)
    # st.toast(f"Usando modelo: {model_name}") # Descomentar para ver cuál usa
    
    genai.configure(api_key=api_key)
    crit = RUBRICA[role]
    
    prompt = f"""
    Rol: Headhunter Senior. Analiza CV para {role} en {faculty}.
    RÚBRICA:
    1. Formación: {crit['Formación']}
    2. Experiencia: {crit['Experiencia']}
    3. Competencias: {crit['Competencias']}
    4. Software: {crit['Software']}
    INSTRUCCIÓN: Devuelve SOLO JSON válido.
    {{
        "nombre": "Nombre Apellido",
        "ajuste": "Alto/Medio/Bajo",
        "razon": "Frase corta",
        "rec": "Avanza/Dudoso/Descartado",
        "n_form": 0.0, "n_exp": 0.0, "n_comp": 0.0, "n_soft": 0.0,
        "comentarios": "Texto plano sin markdown."
    }}
    CV: {text[:15000]}
    """

    try:
        model = genai.GenerativeModel(model_name)
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
        # Si falla, imprimimos el error pero no rompemos la app
        print(f"Error IA: {e}")
        return None

# --- 5. PDF REPORT ---
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
        self.cell(0, 10, f'Generado el {datetime.now().year}', 0, 0, 'C')

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
    
    pdf.set_fill_color(240, 240, 240)
    pdf.rect(10, 50, 190, 30, 'F')
    pdf.set_xy(15, 55)
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(0)
    pdf.cell(90, 10, f"Puntaje Global: {data['final']} / 5.0")
    pdf.cell(0, 10, txt(f"Decisión: {data.get('rec', '')}"), 0, 1)
    
    pdf.set_xy(15, 65)
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 10, txt(f"Ajuste: {data.get('ajuste', '')} | {data.get('razon', '')}"), 0, 1)
    pdf.ln(25)
    
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(52, 152, 219)
    pdf.set_text_color(255)
    pdf.cell(140, 8, "Dimensión", 1, 0, 'L', True)
    pdf.cell(50, 8, "Nota", 1, 1, 'C', True)
    pdf.set_text_color(0)
    pdf.set_font('Arial', '', 10)
    
    items = [("Formación", 'n_form'), ("Experiencia", 'n_exp'), ("Competencias", 'n_comp'), ("Software", 'n_soft')]
    for label, key in items:
        pdf.cell(140, 8, txt(label), 1)
        pdf.cell(50, 8, str(data.get(key, 0)), 1, 1, 'C')
        
    pdf.ln(10)
    pdf.multi_cell(0, 6, txt(data.get('comentarios', '')))
    return pdf.output(dest='S').encode('latin-1')

# --- 6. LOGICA BATCH ---
def run_batch_process(files, faculty, role, api_key, status_container):
    count = 0
    if not files: return 0
    
    for f in files:
        with status_container:
            st.text(f"⏳ Leyendo: {f.name}...")
            text = read_file(f)
            
            if len(text) < 50:
                st.warning(f"⚠️ {f.name}: Archivo vacío.")
                continue
            
            st.text(f"🤖 Analizando: {f.name}...")
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
                st.success(f"✅ {f.name} OK")
            else:
                st.error(f"❌ {f.name}: Error IA (Verifica API Key)")
            
    return count

# --- 7. INTERFAZ ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/9187/9187604.png", width=50)
    st.title("Admin Console")
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("✅ Conectado")
    else:
        api_key = st.text_input("🔑 API Key", type="password")

st.markdown("## 🏢 HR Intelligence Suite")

tab_load, tab_dash, tab_data, tab_repo = st.tabs(["⚡ Procesamiento", "📊 Dashboard", "🗃️ Datos", "📂 PDFs"])

with tab_load:
    st.info("Cargue CVs y procese lotes.")
    col1, col2 = st.columns(2)
    
    def draw_batch(col, idx, color_class):
        with col:
            st.markdown(f'<div class="batch-card {color_class}"><div class="batch-header">📂 Lote #{idx}</div>', unsafe_allow_html=True)
            files = st.file_uploader(f"Files {idx}", type=['pdf','docx'], key=f"u_{idx}", accept_multiple_files=True, label_visibility="collapsed")
            c_a, c_b = st.columns(2)
            fac = c_a.selectbox("Facultad", ["Ingeniería", "Economía", "Ciencias Vida", "Educación"], key=f"f_{idx}")
            rol = c_b.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"], key=f"r_{idx}")
            
            if st.button(f"▶ Procesar Lote {idx}", key=f"btn_{idx}"):
                if not api_key: st.error("Falta API Key")
                elif not files: st.warning("Vacío")
                else:
                    status_box = st.container()
                    n = run_batch_process(files, fac, rol, api_key, status_box)
                    if n > 0:
                        st.success(f"Lote {idx}: {n} procesados.")
                        time.sleep(1)
                        st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    draw_batch(col1, 1, "border-1")
    draw_batch(col2, 2, "border-2")
    draw_batch(col1, 3, "border-3")
    draw_batch(col2, 4, "border-4")

    st.markdown("---")
    if st.button("🚀 PROCESAR TODO", type="primary"):
        if not api_key:
            st.error("Falta API Key")
        else:
            total = 0
            status_global = st.container()
            for i in range(1, 5):
                files = st.session_state.get(f"u_{i}")
                fac = st.session_state.get(f"f_{i}")
                role = st.session_state.get(f"r_{i}")
                if files:
                    with status_global:
                        st.markdown(f"**--- Lote {i} ---**")
                        n = run_batch_process(files, fac, role, api_key, status_global)
                        total += n
            if total > 0:
                st.balloons()
                st.success(f"Total: {total} registros.")
                time.sleep(2)
                st.rerun()
            else:
                st.warning("No hay archivos.")

with tab_dash:
    df = st.session_state.db
    if not df.empty:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total", len(df))
        k2.metric("Promedio", f"{df['Nota'].mean():.2f}")
        k3.metric("Aptos", len(df[df['Estado'] == 'Avanza']))
        k4.metric("Facultad Top", df['Facultad'].mode()[0])
        st.markdown("---")
        g1, g2 = st.columns([2,1])
        with g1:
            fig = px.box(df, x="Facultad", y="Nota", color="Facultad", template="plotly_dark")
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
        with g2:
            fig2 = px.pie(df, names="Estado", hole=0.4, color="Estado", template="plotly_dark", color_discrete_map={'Avanza':'#2ecc71', 'Dudoso':'#f1c40f', 'Descartado':'#e74c3c'})
            fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig2, use_container_width=True)
        st.dataframe(df[df['Nota']>=3.8][['Candidato','Cargo','Facultad','Nota','Estado']], hide_index=True, use_container_width=True)
    else: st.info("Sin datos.")

with tab_data:
    df = st.session_state.db
    if not df.empty: st.dataframe(df.drop(columns=['PDF', 'n_form', 'n_exp', 'n_comp', 'n_soft']), use_container_width=True)
    else: st.info("Vacío.")

with tab_repo:
    df = st.session_state.db
    if not df.empty:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            for i, r in df.iterrows():
                clean = re.sub(r'[^a-zA-Z0-9]', '_', r['Candidato'])
                zf.writestr(f"{clean}.pdf", r['PDF'])
        st.download_button("📦 Descargar ZIP", zip_buf.getvalue(), "Informes.zip", "application/zip", type="primary")
        st.markdown("---")
        for i, r in df.iterrows():
            c1, c2 = st.columns([4,1])
            c1.write(f"📄 {r['Candidato']}")
            c2.download_button("Bajar", r['PDF'], f"{r['Candidato']}.pdf", key=f"d_{i}")
    else: st.info("Sin informes.")
