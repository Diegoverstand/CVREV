import streamlit as st
import pandas as pd
from PyPDF2 import PdfReader
from docx import Document
import google.generativeai as genai
import io
import time
import zipfile
from datetime import datetime
from fpdf import FPDF
import plotly.express as px
import plotly.graph_objects as go

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Analytics CV Académico", layout="wide", page_icon="🎓")

# --- ESTILOS CSS ---
st.markdown("""
    <style>
    .metric-card { background-color: #f0f2f6; border-radius: 10px; padding: 15px; text-align: center; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 4px; }
    .stTabs [aria-selected="true"] { background-color: #4CAF50; color: white; }
    </style>
""", unsafe_allow_html=True)

# --- 1. RÚBRICA Y CONFIGURACIÓN ---
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

# --- 2. GENERADOR PDF ---
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 10, 'Informe Oficial de Evaluación Curricular', 0, 1, 'C')
        self.line(10, 20, 200, 20)
        self.ln(5)
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Generado el {datetime.now().strftime("%d/%m/%Y")} - Pág {self.page_no()}', 0, 0, 'C')

def create_pdf(data):
    pdf = PDFReport()
    pdf.add_page()
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, f"Postulante: {data['Candidato']}", 0, 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 6, f"Facultad: {data['Facultad']} | Cargo: {data['Cargo']}", 0, 1)
    pdf.ln(5)
    
    # Caja Resultado
    pdf.set_fill_color(240, 240, 240)
    pdf.rect(10, 45, 190, 25, 'F')
    pdf.set_xy(15, 48)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(90, 10, f"Puntaje: {data['Puntaje_Final']} / 5.0")
    pdf.cell(90, 10, f"Recomendación: {data['Recomendación']}", 0, 1)
    pdf.set_xy(15, 58)
    pdf.set_font('Arial', 'I', 10)
    pdf.cell(0, 10, f"Ajuste Global: {data['Ajuste']}", 0, 1)
    
    pdf.ln(15)
    # Tabla
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(90, 8, "Dimensión", 1)
    pdf.cell(30, 8, "Nota", 1, 0, 'C')
    pdf.cell(70, 8, "Evidencia Resumida", 1, 1)
    pdf.set_font('Arial', '', 9)
    
    dims = [
        ("Formación (35%)", data['Nota_Formacion']),
        ("Experiencia (30%)", data['Nota_Experiencia']),
        ("Competencias (20%)", data['Nota_Competencias']),
        ("Software (15%)", data['Nota_Software'])
    ]
    for name, score in dims:
        pdf.cell(90, 8, name, 1)
        pdf.cell(30, 8, str(score), 1, 0, 'C')
        pdf.cell(70, 8, "Ver detalle en anexo cualitativo", 1, 1)

    pdf.ln(5)
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(0, 10, "Análisis Cualitativo de la IA", 0, 1)
    pdf.set_font('Arial', '', 10)
    text = data['Comentarios_Texto'].encode('latin-1', 'replace').decode('latin-1')
    pdf.multi_cell(0, 6, text)
    
    return pdf.output(dest='S').encode('latin-1')

# --- 3. LÓGICA DE DATOS MEJORADA (REEMPLAZA ESTO EN TU CÓDIGO) ---
import json
import re

def analyze_gemini(text, role, faculty, api_key):
    try:
        # Configuración del modelo
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        crit = RUBRICA[role]
        
        # Prompt reforzado para evitar errores de formato
        prompt = f"""
        Actúa como un reclutador experto. Analiza este CV (puede estar en inglés o español) para el cargo: {role} en la facultad: {faculty}.
        
        RÚBRICA DE EVALUACIÓN (0 a 5 ptos):
        1. Formación: {crit['Formación']}
        2. Experiencia: {crit['Experiencia']}
        3. Competencias: {crit['Competencias']}
        4. Software: {crit['Software']}

        INSTRUCCIONES TÉCNICAS (CRÍTICO):
        - Responde ÚNICAMENTE con un objeto JSON válido.
        - NO uses bloques de código markdown (```json).
        - NO incluyas texto antes ni después del JSON.
        - Si el texto contiene comillas dobles ("), cámbialas por comillas simples (') para no romper el JSON.
        
        ESTRUCTURA JSON REQUERIDA:
        {{
            "nombre": "Nombre y Apellido",
            "ajuste": "Alto/Medio/Bajo",
            "razon": "Breve justificación del ajuste",
            "rec": "Avanza/Dudoso/Descartado",
            "n_form": 0.0, 
            "n_exp": 0.0, 
            "n_comp": 0.0, 
            "n_soft": 0.0,
            "comentarios": "Resumen de fortalezas y debilidades."
        }}

        CV A ANALIZAR: 
        {text[:12000]}
        """
        
        # Generar respuesta
        res = model.generate_content(prompt)
        raw_text = res.text
        
        # --- LIMPIEZA AVANZADA DE LA RESPUESTA ---
        # 1. Encontrar dónde empieza y termina el JSON real
        start_idx = raw_text.find('{')
        end_idx = raw_text.rfind('}') + 1
        
        if start_idx == -1 or end_idx == 0:
            return None # No se encontró estructura JSON

        json_str = raw_text[start_idx:end_idx]
        
        # 2. Intentar parsear con librería oficial JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Si falla, intentar limpieza agresiva (comas finales, saltos de linea)
            # Esto corrige errores comunes de las IAs
            try:
                # Truco: eval de python es más permisivo que json estricto
                data = eval(json_str) 
            except:
                return None

        # 3. Calcular nota final asegurando que sean números (floats)
        def safe_float(val):
            try: return float(val)
            except: return 0.0

        final = (safe_float(data.get('n_form', 0))*0.35 + 
                 safe_float(data.get('n_exp', 0))*0.30 + 
                 safe_float(data.get('n_comp', 0))*0.20 + 
                 safe_float(data.get('n_soft', 0))*0.15)
        
        # Retornar diccionario limpio con la nota calculada
        return {
            "nombre": data.get('nombre', 'Postulante'),
            "ajuste": data.get('ajuste', 'N/A'),
            "razon": data.get('razon', 'Sin razón'),
            "rec": data.get('rec', 'Revisar'),
            "n_form": safe_float(data.get('n_form', 0)),
            "n_exp": safe_float(data.get('n_exp', 0)),
            "n_comp": safe_float(data.get('n_comp', 0)),
            "n_soft": safe_float(data.get('n_soft', 0)),
            "comentarios": data.get('comentarios', 'Sin comentarios'),
            "final": round(final, 2)
        }

    except Exception as e:
        # Esto imprimirá el error real en la consola de Streamlit Cloud (logs) si falla
        print(f"ERROR CRÍTICO EN ANALYZE_GEMINI: {e}")
        return None

# --- 4. INICIALIZACIÓN DE ESTADO ---
if 'history' not in st.session_state:
    st.session_state.history = pd.DataFrame(columns=[
        "Fecha_Carga", "Candidato", "Facultad", "Cargo", "Puntaje_Final", 
        "Recomendación", "Ajuste", "Comentarios_Texto", 
        "Nota_Formacion", "Nota_Experiencia", "Nota_Competencias", "Nota_Software"
    ])
if 'pdfs' not in st.session_state:
    st.session_state.pdfs = {}

# --- 5. INTERFAZ ---

with st.sidebar:
    st.title("⚙️ Panel de Control")
    if 'GOOGLE_API_KEY' in st.secrets:
        api_key = st.secrets['GOOGLE_API_KEY']
        st.success("✅ API Key Corporativa Activa")
    else:
        api_key = st.text_input("API Key", type="password")
    
    st.divider()
    st.subheader("Cargar Histórico")
    uploaded_history = st.file_uploader("Subir Excel de sesión anterior", type=['xlsx'])
    if uploaded_history:
        try:
            df_hist = pd.read_excel(uploaded_history)
            st.session_state.history = df_hist
            st.success("Historial recuperado")
        except: st.error("Error en archivo")

st.header("🏫 Sistema de Inteligencia de Selección Académica")

# PESTAÑAS
tab_dash, tab_proc, tab_db, tab_repo = st.tabs([
    "📊 Dashboard Ejecutivo", "⚡ Procesamiento IA", "🗃️ Base de Datos Histórica", "📂 Repositorio Digital"
])

# --- TAB 1: DASHBOARD ---
with tab_dash:
    df = st.session_state.history
    if not df.empty:
        # KPI ROW
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Candidatos", len(df))
        c2.metric("Promedio General", f"{df['Puntaje_Final'].mean():.2f}")
        c3.metric("Tasa Aprobación (Sugerida)", f"{len(df[df['Puntaje_Final']>=3.75])} / {len(df)}")
        c4.metric("Facultad + Activa", df['Facultad'].mode()[0] if not df.empty else "N/A")
        
        st.divider()
        
        # GRAFICOS ROW 1
        g1, g2 = st.columns(2)
        with g1:
            st.subheader("Distribución por Facultad")
            fig_bar = px.bar(df, x='Facultad', color='Recomendación', barmode='group', 
                             color_discrete_map={'Avanza':'#4CAF50', 'Dudoso':'#FFC107', 'Descartado':'#F44336'})
            st.plotly_chart(fig_bar, use_container_width=True)
        
        with g2:
            st.subheader("Dispersión de Puntajes")
            fig_hist = px.histogram(df, x="Puntaje_Final", nbins=10, color="Cargo", title="Histograma de Notas")
            st.plotly_chart(fig_hist, use_container_width=True)
            
        # GRAFICOS ROW 2
        g3, g4 = st.columns([2,1])
        with g3:
            st.subheader("Ranking Mejores Candidatos (>4.0)")
            top_cand = df[df['Puntaje_Final'] >= 4.0].sort_values(by='Puntaje_Final', ascending=False).head(10)
            st.dataframe(top_cand[['Candidato', 'Cargo', 'Facultad', 'Puntaje_Final', 'Recomendación']], hide_index=True, use_container_width=True)
        
        with g4:
            st.subheader("Estado General")
            pie_data = df['Recomendación'].value_counts().reset_index()
            fig_pie = px.pie(pie_data, values='count', names='Recomendación', hole=0.4, 
                             color='Recomendación', color_discrete_map={'Avanza':'#4CAF50', 'Dudoso':'#FFC107', 'Descartado':'#F44336'})
            st.plotly_chart(fig_pie, use_container_width=True)
            
    else:
        st.info("⚠️ El Dashboard está vacío. Ve a la pestaña 'Procesamiento IA' y carga CVs para ver las analíticas.")

# --- TAB 2: PROCESAMIENTO (CÓDIGO MEJORADO) ---
with tab_proc:
    st.markdown("### ⚡ Centro de Procesamiento")
    col_input, col_conf = st.columns([3, 1])
    
    with col_input:
        files = st.file_uploader("1. Arrastra los CVs aquí (PDF/Word)", accept_multiple_files=True, type=['pdf','docx'])
    
    with col_conf:
        st.write("2. Configura el Lote:")
        sel_fac = st.selectbox("Facultad", ["Ingeniería", "Economía", "Ciencias Vida", "Educación"])
        sel_rol = st.selectbox("Cargo", ["Docente", "Investigador", "Gestión Académica"])
    
    # Botón de acción
    if st.button("🚀 Ejecutar Análisis", type="primary"):
        if not files:
            st.warning("⚠️ Debes cargar al menos un archivo.")
        elif not api_key:
            st.error("❌ Falta la API Key en el menú lateral.")
        else:
            # Contenedor para mostrar el reporte en vivo
            status_container = st.container()
            bar = st.progress(0)
            success_count = 0
            
            with status_container:
                st.write("Iniciando motor de IA...")
                
                for i, f in enumerate(files):
                    # Intentar leer
                    txt = read_file(f)
                    
                    # Validar si tiene texto
                    if len(txt) < 50:
                        st.warning(f"⚠️ **{f.name}**: Archivo vacío o es una imagen escaneada. Se omitió.")
                        continue
                        
                    # Intentar analizar con Gemini
                    try:
                        d = analyze_gemini(txt, sel_rol, sel_fac, api_key)
                        
                        if d:
                            # Guardar en historial
                            new_row = {
                                "Fecha_Carga": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "Candidato": d.get('nombre', 'Desconocido'),
                                "Facultad": sel_fac,
                                "Cargo": sel_rol,
                                "Puntaje_Final": d.get('final', 0),
                                "Recomendación": d.get('rec', 'N/A'),
                                "Ajuste": d.get('ajuste', 'N/A'),
                                "Comentarios_Texto": d.get('comentarios', ''),
                                "Nota_Formacion": d.get('n_form', 0),
                                "Nota_Experiencia": d.get('n_exp', 0),
                                "Nota_Competencias": d.get('n_comp', 0),
                                "Nota_Software": d.get('n_soft', 0)
                            }
                            st.session_state.history = pd.concat([st.session_state.history, pd.DataFrame([new_row])], ignore_index=True)
                            
                            # Generar PDF
                            pdf_bytes = create_pdf(new_row)
                            st.session_state.pdfs[f"{d['nombre']}_{int(time.time())}"] = pdf_bytes
                            
                            st.success(f"✅ **{f.name}**: Procesado correctamente (Nota: {new_row['Puntaje_Final']})")
                            success_count += 1
                        else:
                            st.error(f"❌ **{f.name}**: La IA no pudo extraer datos válidos.")
                            
                    except Exception as e:
                        st.error(f"❌ **{f.name}**: Error técnico ({str(e)})")
                    
                    # Actualizar barra
                    bar.progress((i+1)/len(files))
            
            if success_count > 0:
                st.balloons()
                st.success(f"¡Listo! Se procesaron {success_count} CVs exitosamente.")
                st.info("👉 Ve a la pestaña **'Dashboard Ejecutivo'** o **'Base de Datos'** para ver los resultados.")
            else:
                st.error("No se pudo procesar ningún CV correctamente.")

# --- TAB 3: BASE DE DATOS ---
with tab_db:
    st.subheader("Registro Histórico de Evaluaciones")
    if not st.session_state.history.empty:
        # Filtros
        f1, f2 = st.columns(2)
        filtro_fac = f1.multiselect("Filtrar por Facultad", st.session_state.history['Facultad'].unique())
        filtro_rec = f2.multiselect("Filtrar por Recomendación", st.session_state.history['Recomendación'].unique())
        
        df_show = st.session_state.history.copy()
        if filtro_fac: df_show = df_show[df_show['Facultad'].isin(filtro_fac)]
        if filtro_rec: df_show = df_show[df_show['Recomendación'].isin(filtro_rec)]
        
        st.dataframe(
            df_show,
            column_config={
                "Puntaje_Final": st.column_config.ProgressColumn("Nota", min_value=0, max_value=5, format="%.2f"),
                "Fecha_Carga": st.column_config.TextColumn("Fecha"),
                "Comentarios_Texto": st.column_config.TextColumn("Notas", width="large")
            },
            hide_index=True,
            use_container_width=True
        )
        
        # Exportar Excel para Persistencia
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            st.session_state.history.to_excel(writer, index=False)
        
        st.download_button("💾 Descargar Base de Datos Completa (Excel)", buffer.getvalue(), "Historial_CVs.xlsx")
    else:
        st.write("No hay datos aún.")

# --- TAB 4: REPOSITORIO ---
with tab_repo:
    if st.session_state.pdfs:
        st.subheader("Descarga de Informes Generados")
        
        # ZIP
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            for name, content in st.session_state.pdfs.items():
                zf.writestr(f"{name}.pdf", content)
        
        st.download_button("📦 Descargar TODO (ZIP)", zip_buf.getvalue(), "Informes.zip", type="primary")
        
        st.divider()
        # Lista Individual
        for name, content in st.session_state.pdfs.items():
            col1, col2 = st.columns([4,1])
            col1.write(f"📄 Informe: {name}")
            col2.download_button("Descargar", content, f"{name}.pdf", key=name)
    else:

        st.info("Los PDFs generados aparecerán aquí.")

