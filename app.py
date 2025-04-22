# -*- coding: utf-8 -*-
"""
AI Judge - Fekrety Innovation Evaluator

This Streamlit app allows you to submit two files:
  1. A Template Excel file containing project details (e.g. project title, submitter name, email, phone, beneficiaries, etc.).
  2. A Pitch Deck PPTX file containing the pitch deck information:
     - Slide 1: NSV information.
     - Slide 2: Value creation, functionality, insights, next steps (with possible product–price–place–promotion info).

The app displays the submission details from the Excel file and evaluates the idea via a multi-agent system.
The complete agent thought process is shown with a typewriter effect,
and the final evaluation is displayed in Markdown and available for download as a PDF file.
"""

import os
import sys
import io
import re
import json
import time
import base64
import pandas as pd
from dotenv import load_dotenv
import streamlit as st
import warnings
import streamlit.components.v1 as components
from streamlit.components.v1 import html

# Import PDF generation library
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.units import inch
import datetime
import zipfile
from pptx import Presentation
from lxml import etree
from io import BytesIO
from lxml import etree


warnings.filterwarnings('ignore')

# Import packages for reading various file formats
import PyPDF2
from docx import Document
import pptx

# Import multi-agent system components
from crewai import Agent, Task, Crew
from langchain_openai import ChatOpenAI
from openai import OpenAI

# --- Global Configuration ---
load_dotenv()
api_key = os.getenv("API_KEY")
os.environ["OPENAI_API_KEY"] = api_key

serper_api_key = os.getenv("SERPER_API_KEY")
os.environ["SERPER_API_KEY"] = serper_api_key

from crewai_tools import ScrapeWebsiteTool, SerperDevTool

# Initialize the tools
search_tool = SerperDevTool(recency_days=365)  # only fetch results from the past year
scrape_tool = ScrapeWebsiteTool()

# Define available models for selection.
AVAILABLE_MODELS = {
    "GPT-4.1": "gpt-4.1",
    "GPT-4.1-mini": "gpt-4.1-mini",
    "GPT-4o-mini": "gpt-4o-mini",
    "GPT-4o": "gpt-4o",
    "GPT-o3": "o3",
    "GPT-04-mini": "o4-mini"
}
# Set a default model (will be overridden by user selection).
default_model_choice = "GPT-4o-mini"

# --- Function Definitions ---

def initialize_ai_clients(model_name):
    """Initialize the API client and language model."""
    client = OpenAI(api_key=api_key)
    llm = ChatOpenAI(model=model_name, api_key=api_key)
    return client, llm

def clean_output(raw_text: str) -> str:
    """Remove ANSI escape sequences from text."""
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', raw_text)

def read_xlsx(file):
    df = pd.read_excel(file)
    return df


def read_pptx(file) -> str:
    """
    Extract literally *all* text from a PPTX—including SmartArt, tables, grouped shapes, etc.—by
    walking every <a:t> element in every slide part.
    """
    # load into python-pptx to get slides
    pptx_io = BytesIO(file.read())
    prs = Presentation(pptx_io)
    all_text = []

    # first, grab anything python-pptx already surfaces
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    all_text.append(para.text.strip())

    # now, as a catch-all, scan *all* the slide XMLs for <a:t> nodes
    pptx_io.seek(0)
    with zipfile.ZipFile(pptx_io) as z:
        # any XML under ppt/slides or ppt/diagrams
        for name in z.namelist():
            if name.endswith(".xml") and (name.startswith("ppt/slides") or name.startswith("ppt/diagrams")):
                xml = z.read(name)
                root = etree.fromstring(xml)
                # the DrawingML namespace for text is always this
                for t in root.xpath("//a:t", namespaces={"a":"http://schemas.openxmlformats.org/drawingml/2006/main"}):
                    if t.text and t.text.strip():
                        all_text.append(t.text.strip())

    # de‐duplicate and join
    # (you can skip dedupe if order matters)
    seen = set()
    cleaned = []
    for line in all_text:
        if line not in seen:
            seen.add(line)
            cleaned.append(line)

    return "\n".join(cleaned)

def read_pdf(file):
    pdf_reader = PyPDF2.PdfReader(file)
    text = ""
    for page in pdf_reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text
    return text

def read_docx(file):
    doc = Document(file)
    text = ""
    for para in doc.paragraphs:
        text += para.text + "\n"
    return text

def read_txt(file):
    return file.read().decode("utf-8")

def create_pdf_report(idea_title, score, justification, template_df):
    """
    Create a PDF report with evaluation results - with four separate tables:
    - First table with 3 columns
    - Three subsequent tables with 2 columns each
    All arranged vertically one below another
    """
    buffer = io.BytesIO()
    # Set page margins to ensure content fits properly
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                           rightMargin=72, leftMargin=72,
                           topMargin=72, bottomMargin=72)
    styles = getSampleStyleSheet()
    
    # Create a custom style for the title
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=18,
        alignment=1,  # Center alignment
        spaceAfter=12
    )
    
    # Create a custom style for section headings
    section_style = ParagraphStyle(
        'Section',
        parent=styles['Heading2'],
        fontSize=14,
        spaceBefore=10,
        spaceAfter=6
    )
    
    # Create a custom style for the score display
    score_style = ParagraphStyle(
        'Score',
        parent=styles['Normal'],
        fontSize=24,
        alignment=1,  # Center alignment
        textColor=colors.blue
    )
    
    # Build the PDF content
    elements = []
    
    # Add logo if available
    try:
        logo = Image("judge_bg.png", width=1.5*inch, height=1.5*inch)
        elements.append(logo)
    except:
        pass  # Continue without logo if not found
    
    # Add title and date
    elements.append(Paragraph("AI Judge - Innovation Evaluation Report", title_style))
    elements.append(Paragraph(f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    elements.append(Spacer(1, 0.25*inch))
    
    # Add idea title
    elements.append(Paragraph(f"Idea: {idea_title}", section_style))
    elements.append(Spacer(1, 0.1*inch))
    
    # Add score
    elements.append(Paragraph(f"Overall Score: {score}/100", score_style))
    elements.append(Spacer(1, 0.25*inch))
    
    # Add submission details section heading
    elements.append(Paragraph("Submission Details:", section_style))
    
    # Get all columns from the dataframe
    all_columns = list(template_df.columns)
    
    # Common table style
    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('WORDWRAP', (0, 0), (-1, -1), True),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6)
    ])
    
    total_cols = len(all_columns)
    cols_used = 0
    
    # Table 1: First 3 columns (or fewer if not available)
    cols_table1 = min(3, total_cols)
    table1_cols = all_columns[:cols_table1]
    cols_used += cols_table1
    
    # Table 2: Next 2 columns (or fewer if not available)
    cols_table2 = min(2, max(0, total_cols - cols_used))
    table2_cols = all_columns[cols_used:cols_used+cols_table2] if cols_table2 > 0 else []
    cols_used += cols_table2
    
    # Table 3: Next 2 columns (or fewer if not available)
    cols_table3 = min(2, max(0, total_cols - cols_used))
    table3_cols = all_columns[cols_used:cols_used+cols_table3] if cols_table3 > 0 else []
    cols_used += cols_table3
    
    # Table 4: Next 2 columns (or fewer if not available)
    cols_table4 = min(2, max(0, total_cols - cols_used))
    table4_cols = all_columns[cols_used:cols_used+cols_table4] if cols_table4 > 0 else []
    
    def create_table(table_cols):
        if not table_cols:
            return None
        table_data = [table_cols]
        for _, row in template_df.iterrows():
            data_row = []
            for col in table_cols:
                data_row.append(str(row[col]))
            table_data.append(data_row)
        col_width = doc.width / len(table_cols)
        table = Table(table_data, colWidths=[col_width] * len(table_cols))
        table.setStyle(table_style)
        return table
    
    tables = [
        (table1_cols, "Table 1 (Primary Details)"),
        (table2_cols, "Table 2 (Additional Details)"),
        (table3_cols, "Table 3 (Additional Details)"),
        (table4_cols, "Table 4 (Additional Details)")
    ]
    
    tables_added = 0
    for i, (cols, table_name) in enumerate(tables):
        table = create_table(cols)
        if table:
            tables_added += 1
            elements.append(table)
            elements.append(Spacer(1, 0.15*inch))
    
    if tables_added == 0:
        elements.append(Paragraph("No submission details available", styles["Normal"]))
    
    elements.append(Spacer(1, 0.25*inch))
    elements.append(Paragraph("Evaluation Justification:", section_style))
    
    justification_style = ParagraphStyle(
        'Justification',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6,
        leading=14
    )
    
    formatted_sections = []
    pattern = r"(Creativity|Viability|NSV):?\s*(\d+/\d+)?\s*-?\s*(.*?)(?=(Creativity|Viability|NSV):|$)"
    matches = re.findall(pattern, justification, re.DOTALL)
    
    if matches:
        for section, score_part, content, _ in matches:
            score_text = f"{score_part} - " if score_part else ""
            section_text = f"<b>{section} (Score Component):</b><br/>{score_text}{content.strip()}"
            formatted_sections.append(Paragraph(section_text, justification_style))
    else:
        sections = {"Creativity": "", "Viability": "", "NSV": ""}
        current_section = None
        for line in justification.split(", "):
            for section in sections.keys():
                if line.startswith(section + ":"):
                    current_section = section
                    content = line[len(section) + 1:].strip()
                    sections[section] = content
                    break
            if current_section and not line.startswith(tuple([s + ":" for s in sections.keys()])):
                sections[current_section] += ", " + line
        for section, content in sections.items():
            if content:
                section_text = f"<b>{section} (Score Component):</b><br/>{content}"
                formatted_sections.append(Paragraph(section_text, justification_style))
    
    for section_paragraph in formatted_sections:
        elements.append(section_paragraph)
        elements.append(Spacer(1, 0.1*inch))
    
    elements.append(Spacer(1, 0.5*inch))
    elements.append(Paragraph("This evaluation was generated by AI Judge - Gulf Bank's Innovation Evaluator", ParagraphStyle("Footer", parent=styles["Italic"], alignment=1)))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer

def process_idea(idea_text, llm, excel_title):
    """
    Process the idea text using the multi-agent system.
    
    Agents:
      - Idea Analyzer: Extracts details on creativity, viability, and NSV.
      - Idea Scorer: Scores the idea with a detailed breakdown.
      
    Returns:
      - The idea title (from Excel), score, and justification.
      - The full console output (agent thought process) as a string.
    """
    # --- Inside process_idea (before creating tasks) ---
    summary_extractor_agent = Agent(
    role="Idea Summarizer",
    goal=(
        "Read the entire idea pitch and produce a comprehensive summary of the idea"
        "that captures its core concept and objectives."
    ),
    verbose=True,
    allow_delegation=False,
    backstory=(
        "You are a concise executive summary writer. You have a vast knowledge about banking and Kuwait's financial and social conditions."
        "Your job is to distill long submissions into a comprehensive overview."
    )
    )

    idea_analyzer_agent = Agent(
        role="Idea Analyzer",
        goal=(
            "Analyze the idea based on the summary received from the summary_extractor_agent  "
            "to pull out key insights on Creativity, Viability, and NSV (Need, Solution, Value). "
            "If you need external industry context—Kuwaiti banking trends, best practices, competitor moves—"
            "search the internet. When searching the internet, your query should be a summary of the entire project. "
        ),
        verbose=True,
        allow_delegation=False,
        backstory=(
            "You are a senior Gulf Bank executive, expert in Kuwaiti banking. You know our Vision & Mission. "
            "Read the idea from the summary extractor agent "
            "Wheb you search the internet, ignore the title of the project. your search query should be extracted from the content of the pitch"
            "Then summarize how Creative, Viable, and Strategic this idea truly is."
        ),
        tools = [scrape_tool, search_tool]   # <— now your agent can do live searches
    )
    
    idea_scorer_agent = Agent(
        role="Idea Scorer",
        goal=(
            "Score the idea on a scale of 1 to 100 based on the following criteria:\n"
            "1. Creativity (35 points): Originality and uniqueness.\n"
            "2. Viability (35 points): Practical feasibility and clarity of the implementation plan.\n"
            "3. NSV (30 points): Overall value based on Need, Solution, and Value.\n\n"
            "Be highly critical: reserve 0–40 for poor ideas, 41–60 for average ideas, 61–80 for good ideas, and 81–100 only for truly exceptional, implementable, high‑impact ideas. "
            "Provide a detailed breakdown with justification for each criterion, explicitly calling out any weaknesses or risks that lowered the score. "
            "Return your result in JSON format exactly like this (without additional text):\n\n"
            "{{\n"
            '  "score": <score>,\n'
            '  "justification": "Creativity: <explanation>, Viability: <explanation>, NSV: <explanation>"\n'
            "}}\n"
        ),
        verbose=True,
        allow_delegation=False,
        backstory=(
            "You are a veteran Gulf Bank executive and tough innovation judge. "
            "You understand that mediocrity is common—only the very best ideas should score above 80. "
            "When scoring, you must clearly delineate what makes an idea poor, average, good, or excellent, "
            "and call out any gaps or risks that push an idea into a lower bracket."
        )
    )
    
    summary_task = Task(
        description="Generate a comprehensive summary of the submitted idea ({idea_text}).",
        expected_output="A comprehensive paragraph summarizing the core concept and objectives of the submitted idea.",
        agent=summary_extractor_agent,
    )
    
    
    
    idea_analysis_task = Task(
    description=(
        "Using the **entire** summary provided by the summary_task, extract detailed notes on "
        "Creativity, Viability, and NSV.  "
    ),
    expected_output=(
        "A structured, thorough breakdown of the idea’s innovative aspects, feasibility plan, "
        "pitch quality, and strategic value."
    ),
    agent=idea_analyzer_agent,
    context=[summary_task],
    )
    
    idea_scoring_task = Task(
        description=(
            "Using the analysis from idea_analysis_task, score the idea "
            "on a scale of 1 to 100 based on the following criteria: Creativity (35), Viability (35), and NSV (30): Need, Solution, Value). "
            "Provide a detailed breakdown and justification for each criterion. Return your result in JSON format exactly as described above."
        ),
        expected_output='A JSON object with keys "score" and "justification".',
        context=[idea_analysis_task],
        agent=idea_scorer_agent,
    )
    
    crew_inputs = {'idea_text': idea_text}
    innovation_judging_crew = Crew(
        agents=[summary_extractor_agent,idea_analyzer_agent, idea_scorer_agent],
        tasks=[summary_task,idea_analysis_task, idea_scoring_task],
        manager=llm,
        verbose=True
    )
    
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    raw_result = innovation_judging_crew.kickoff(inputs=crew_inputs)
    sys.stdout = old_stdout
    thought_output = buf.getvalue()
    buf.close()
    
    cleaned_thought = clean_output(thought_output)
    
    try:
        parsed_result = json.loads(str(raw_result))
        score = parsed_result.get("score", None)
        justification = parsed_result.get("justification", None)
    except Exception as e:
        score, justification = None, None
        st.error("Error parsing final JSON output: " + str(e))
    
    return excel_title, score, justification, cleaned_thought

# --- Streamlit UI Setup ---
st.set_page_config(
    page_title="AI Judge - Innovation Evaluator", 
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .stApp {
        background-color: #f8f9fa;
    }
    h1, h2, h3, h4, h5, h6 {
        color: #2c3e50 !important;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }
    .stButton>button {
        background-color: #4a6fa5;
        color: white;
        border-radius: 8px;
        padding: 0.5rem 1rem;
        border: none;
        font-weight: 500;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #3a5a8f;
        transform: translateY(-2px);
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .stFileUploader>div>div {
        border: 2px dashed #4a6fa5;
        border-radius: 12px;
        padding: 2rem;
        background-color: rgba(74, 111, 165, 0.05);
    }
    .stExpander {
        border: 1px solid #e1e4e8;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .stDataFrame {
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .stSuccess {
        border-radius: 12px;
    }
    .custom-card {
        background-color: white;
        border-radius: 12px;
        padding: 1.5rem;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 1.5rem;
        border-left: 4px solid #4a6fa5;
    }
    .agent-thought {
        background-color: #f0f4f8;
        border-radius: 12px;
        padding: 1rem;
        margin: 0.5rem 0;
        font-family: 'Courier New', monospace;
        font-size: 14px;
        line-height: 1.6;
    }
    .score-display {
        font-size: 2.5rem;
        font-weight: bold;
        color: #4a6fa5;
        text-align: center;
        margin: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 3])
with col1:
    st.image("judge_bg.png", width=150)
with col2:
    st.title("⚖️ AI Judge - Innovation Evaluator")
    st.caption("Your AI-powered innovation assessment tool")

with st.container():
    st.markdown("""
    <div class="custom-card">
        <h3 style="color: #2c3e50; margin-top: 0;">Welcome to the Future of Innovation Evaluation! 🚀</h3>
        <p>Our cutting-edge AI delivers an unbiased, detailed evaluation based on:</p>
        <ul>
            <li><b>Creativity</b> (35 points): Originality and uniqueness</li>
            <li><b>Viability</b> (30 points): Practical feasibility</li>
            <li><b>NSV</b> (35 points): Need, Solution, and Value</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

with st.container():
    st.markdown("""
    <div class="custom-card">
        <h4 style="color: #2c3e50; margin-top: 0;">Evaluation Settings</h4>
    """, unsafe_allow_html=True)
    model_choice = st.selectbox("Select AI Model", list(AVAILABLE_MODELS.keys()),
                                index=list(AVAILABLE_MODELS.keys()).index(default_model_choice),
                                help="Choose the AI model that will evaluate your submission")
    st.markdown("</div>", unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    with st.container():
        st.markdown("""
        <div class="custom-card">
            <h4 style="color: #2c3e50; margin-top: 0;">Project Template</h4>
        """, unsafe_allow_html=True)
        template_file = st.file_uploader("Upload Excel Template", type=["xlsx"],
                                         label_visibility="collapsed")
        st.markdown("</div>", unsafe_allow_html=True)

with col2:
    with st.container():
        st.markdown("""
        <div class="custom-card">
            <h4 style="color: #2c3e50; margin-top: 0;">Pitch Deck</h4>
        """, unsafe_allow_html=True)
        pitch_file = st.file_uploader("Upload PowerPoint Pitch", type=["pptx"],
                                      label_visibility="collapsed")
        st.markdown("</div>", unsafe_allow_html=True)

if template_file is not None and pitch_file is not None:
    with st.container():
        st.markdown("""<div class="custom-card">
            <h4 style="color: #2c3e50; margin-top: 0;">Submission Details</h4>""", unsafe_allow_html=True)
        template_df = pd.read_excel(template_file)
        st.dataframe(template_df)
        st.markdown("</div>", unsafe_allow_html=True)
        
        pitch_text = read_pptx(pitch_file)
        st.markdown("### Pitch Deck Details (Extracted Text)")
        st.text_area("Pitch Deck Content", pitch_text, height=300)
    
    if st.button("Evaluate Idea", type="primary"):
        with st.spinner("Evaluating your idea..."):
            client, llm = initialize_ai_clients(AVAILABLE_MODELS[model_choice])
            # Extract the idea title from the Excel file using the "Project Title" header
            excel_title = (template_df["Project Title"].iloc[0] 
                           if "Project Title" in template_df.columns and not template_df["Project Title"].empty 
                           else "Untitled")
            idea_title, score, justification, thought_process = process_idea(pitch_text, llm, excel_title)
            
            st.markdown("### AI Deliberation Process")
            with st.expander("🔍 View Detailed Agent Thought Process", expanded=True):
                with st.chat_message("assistant"):
                    text_placeholder = st.empty()
                    displayed_text = ""
                    for char in thought_process:
                        displayed_text += char
                        processed_text = displayed_text.replace(
                            "Agent:", "<span style='font-weight:bold;color:#2c3e50'>Agent:</span>"
                        ).replace(
                            "Thought:", "<span style='font-weight:bold;color:#2c3e50'>Thought:</span>"
                        ).replace(
                            "Final Answer:", "<span style='font-weight:bold;color:#2c3e50'>Final Answer:</span>"
                        )
                        text_placeholder.markdown(f"""
                        <div style="font-family: 'Courier New', monospace; font-size: 14px; background-color: #f0f4f8; padding: 1rem; border-radius: 12px; margin: 0.5rem 0; line-height: 1.6;">
                            {processed_text}▌
                        </div>
                        """, unsafe_allow_html=True)
                        time.sleep(0.002)
                    
                    text_placeholder.markdown(f"""
                    <div style="font-family: 'Courier New', monospace; font-size: 14px; background-color: #f0f4f8; padding: 1rem; border-radius: 12px; margin: 0.5rem 0; line-height: 1.6;">
                        {processed_text}
                    </div>
                    """, unsafe_allow_html=True)
            
            st.success("Evaluation Complete!")
            formatted_justification = justification.replace("Creativity:", "\n\n**Creativity:**") \
                                                 .replace("Viability:", "\n\n**Viability:**") \
                                                 .replace("NSV:", "\n\n**NSV:**")
            
            st.markdown(f"""
            <div class="custom-card">
                <h2 style="color: #2c3e50; text-align: center; margin-bottom: 0.5rem;">Evaluation Results</h2>
                <h3 style="text-align: center; margin-top: 0;">{idea_title}</h3>
                <div class="score-display" style="margin: 1rem 0;">{score}/100</div>
                <p style="font-weight: bold; color: #2c3e50; margin-bottom: 0.5rem;">Detailed Justification:</p>
                <div style="line-height: 1.8;">
                    {formatted_justification}
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            pdf_buffer = create_pdf_report(idea_title, score, justification, template_df)
            
            st.download_button(
                label="📥 Download Full Report (PDF)",
                data=pdf_buffer,
                file_name=f"evaluation_{idea_title.replace(' ','_')}.pdf",
                mime="application/pdf"
            )
            
            result_json = json.dumps({
                "idea_title": idea_title,
                "score": score,
                "justification": justification
            }, indent=2)
            
