import streamlit as st
import pandas as pd
import re
from paddleocr import PaddleOCR
from fuzzywuzzy import fuzz
import numpy as np
from PIL import Image
import traceback
import os
import time
import requests
from streamlit.components.v1 import html

# Initialize PaddleOCR model
ocr_model = PaddleOCR(
    lang='en', 
    use_gpu=False, 
    use_angle_cls=True, 
    det_db_box_thresh=0.5, 
    det_db_thresh=0.5, 
    show_log=False, 
    use_onnx=False
)

# Path to Excel database
EXCEL_DB_PATH = "medications.xltx"

# OpenFDA API Key (Sign up at https://open.fda.gov/apis/)
OPENFDA_API_KEY = "sUrx5CRuj0IfgElNS3II5uu2cYSrK0x98u9BAkiR"  # Replace with your OpenFDA API key
OPENFDA_BASE_URL = "https://api.fda.gov/drug/label.json"

# Cache interactions to avoid repeated API calls
INTERACTION_CACHE = {}

def load_medications():
    """Load medications from an Excel file"""
    if not os.path.exists(EXCEL_DB_PATH):
        st.error("Medications database not found!")
        return []

    try:
        df = pd.read_excel(EXCEL_DB_PATH, engine="openpyxl")
        return df.to_dict(orient="records")
    except Exception as e:
        st.error("Failed to load Excel file.")
        st.text(traceback.format_exc())
        return []



def check_drug_name_in_db(ocr_text, medications):
    """Return sorted list of form, dosage, and name matches with score"""
    matches = []
    
    # Extract dosage (number only or number with unit) and form (including plural variations)
    dosage_pattern = r'\b(\d{1,4})\b'  # Matches numbers, including multiple digits
    form_pattern = r'\b(comprime|comprimés|gelule|sirop|injection|creme|pommade|suppositoire)\b'
    
    # Extract dosage and form from OCR text
    extracted_dosage = re.findall(dosage_pattern, ocr_text)
    extracted_form = re.findall(form_pattern, ocr_text, re.IGNORECASE)
    
    # Normalize form search (handle plural forms, e.g., "comprimés")
    normalized_forms = ['comprime', 'gelule', 'sirop', 'injection', 'creme', 'pommade', 'suppositoire']
    extracted_form = [f.lower() for f in extracted_form]
    
    # Match plural and singular forms
    extracted_form = [form for form in extracted_form if form in normalized_forms]

    for med in medications:
        med_name = str(med.get('Nom', '')).strip()
        if not med_name:
            continue
        
        # Perform fuzzy matching on the entire OCR text for the name
        name_ratio = fuzz.partial_ratio(ocr_text.lower(), med_name.lower())
        
        # Filter by form and dosage
        med_form = str(med.get('Forme', '')).lower()
        form_match = any(f.lower() == med_form for f in extracted_form) if extracted_form else False
        
        # Calculate dosage score - if dosage is found as a number, compare it with the dosage field
        med_dosage = str(med.get('Dosage', '')).lower()
        dosage_score = 0
        
        if extracted_dosage:
            for dosage in extracted_dosage:
                # Search for exact numeric matches in dosage
                if dosage in med_dosage:
                    dosage_score = 100
                    break
                # For more flexible matches, calculate a ratio
                else:
                    dosage_score = max(fuzz.ratio(dosage, med_dosage) for dosage in extracted_dosage)
        
        # Combine name and dosage scores
        total_score = 0.6 * name_ratio + 0.4 * dosage_score
        
        # Only add medications with a score of 55 or higher
        if total_score >= 55:
            matches.append((med_form, total_score, med))
    
    return sorted(matches, key=lambda x: x[1], reverse=True)

def truncate_text(text, max_length=25):
    """Truncate text with ellipsis if exceeds max length"""
    return (text[:max_length] + '...') if len(text) > max_length else text

def display_med_card(med_data, score, index):
    """Display a medication card with matching details"""
    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{med_data['Nom']}**")
            st.caption(f"**Dosage:** {med_data.get('Dosage', 'N/A')}")
            st.caption(f"**Form:** {med_data.get('Forme', 'N/A')}")
            st.caption(f"**Match Score:** {score:.1f}%")
        with col2:
            color = "#4CAF50" if score > 85 else "#FF9800" if score > 75 else "#F44336"
            st.markdown(
                f"<div style='background-color:{color}; padding: 4px 8px; border-radius: 4px; text-align: center;"
                "font-size: 12px; width: 100%; box-sizing: border-box; margin-top: 8px;'>"
                f"<span style='color: white; font-weight: bold;'>{score:.0f}%</span></div>", 
                unsafe_allow_html=True
            )
        
        if st.button(f"Select →", key=f"select_{index}"):
            st.session_state.selected_med = med_data
            st.session_state.show_preview = True

def display_all_matches(matches):
    """Display all matches in a scrollable container without nested expanders"""
    with st.container(border=True):
        st.subheader("All Matches")
        for idx, (name, score, med) in enumerate(matches):
            st.markdown(f"### #{idx + 1}: {name} (Score: {score:.1f}%)")
            display_med_details(med, use_expander=False)  # Disable expander here
            st.divider()  # Add a divider between matches

def check_drug_interactions(active_ingredient):
    """Check drug interactions using OpenFDA API"""
    if active_ingredient.lower() in INTERACTION_CACHE:
        return INTERACTION_CACHE[active_ingredient.lower()]
    
    try:
        # Query OpenFDA API
        params = {
            "search": f'openfda.substance_name:"{active_ingredient}"',
            "limit": 1,
            "api_key": OPENFDA_API_KEY
        }
        response = requests.get(OPENFDA_BASE_URL, params=params)
        
        if response.status_code != 200:
            st.warning(f"⚠️ Failed to fetch interactions for {active_ingredient}. API Error: {response.status_code}")
            return []
        
        data = response.json()
        if not data.get('results'):
            return []
        
        # Extract interactions from the drug label
        drug_info = data['results'][0]
        interactions = []
        
        # Check for drug interactions section
        if 'drug_interactions' in drug_info:
            interactions.extend(drug_info['drug_interactions'])
        
        # Check for warnings and precautions
        if 'warnings' in drug_info:
            interactions.extend(drug_info['warnings'])
        
        # Cache the results
        INTERACTION_CACHE[active_ingredient.lower()] = interactions
        return interactions
    
    except Exception as e:
        st.error(f"🚨 Error checking interactions: {str(e)}")
        return []

def display_med_details(med_data, use_expander=True):
    """Display detailed medication information with interactions"""
    if use_expander:
        with st.expander("📖 Full Medication Details", expanded=True):
            _display_med_details_content(med_data)
    else:
        _display_med_details_content(med_data)

def _display_med_details_content(med_data):
    """Helper function to display medication details content"""
    cols = st.columns(2)
    with cols[0]:
        st.write("**Basic Information**")
        st.markdown(f"""
        - **Name:** {med_data.get('Nom', 'N/A')}
        - **Dosage:** {med_data.get('Dosage', 'N/A')}
        - **Form:** {med_data.get('Forme', 'N/A')}
        - **Presentation:** {med_data.get('Presentation', 'N/A')}
        - **Active Ingredient:** {med_data.get('DCI', 'N/A')}
        """)
        
    with cols[1]:
        st.write("**Regulatory Information**")
        st.markdown(f"""
        - **Class:** {med_data.get('Classe', 'N/A')}
        - **Sub-class:** {med_data.get('Sous Classe', 'N/A')}
        - **Manufacturer:** {med_data.get('Laboratoire', 'N/A')}
        - **AMM Number:** {med_data.get('AMM', 'N/A')}
        - **Prescription Status:** {med_data.get('G/P/B', 'N/A')}
        """)
    
    st.write("**Additional Information**")
    st.markdown(f"""
    - **Primary Packaging:** {med_data.get('Conditionnement primaire', 'N/A')}
    - **Shelf Life:** {med_data.get('Duree de conservation', 'N/A')}
    - **VEIC Code:** {med_data.get('VEIC', 'N/A')}
    - **Indications:** {med_data.get('Indications', 'N/A')}
    """)
    
    # Drug interactions check
    st.divider()
    dci = med_data.get('DCI', '').split(',')[0].strip()
    if dci:
        interactions = check_drug_interactions(dci)
        if interactions:
            st.error("⚠️ **Potential Drug Interactions**")
            for interaction in interactions:
                st.markdown(f"- {interaction}")
        else:
            st.success("✅ No known dangerous interactions")

def inject_custom_css():
    st.markdown("""
    <style>
        /* Previous CSS styles remain the same */
        
        /* Confirm button styling */
        div[data-testid="stVerticalBlock"] button:has(div:contains('Confirm Selection')) {
            background-color: #4CAF50 !important;
            color: white !important;
            border-color: #388E3C !important;
        }
        
        div[data-testid="stVerticalBlock"] button:has(div:contains('Confirm Selection')):hover {
            background-color: #45a049 !important;
            border-color: #2d6d30 !important;
        }
    </style>
    """, unsafe_allow_html=True)

# Initialize session state
if 'selected_med' not in st.session_state:
    st.session_state.selected_med = None
if 'show_preview' not in st.session_state:
    st.session_state.show_preview = False

# Streamlit UI
st.title("💊 Smart Medication Scanner")
st.write("Upload an image of a medication package to identify it using our AI-powered system")

with st.expander("ℹ️ How it works"):
    st.markdown("""
    1. **Upload** a clear image of the medication name
    2. **Review** the automatically extracted text
    3. **Select** from AI-suggested matches
    4. **Verify** detailed medication information
    """)

# Load medications from Excel
medications = load_medications()

# Inject custom CSS
inject_custom_css()

# Define uploaded_file here
uploaded_file = st.file_uploader(
    "Choose an image", 
    type=["png", "jpg", "jpeg"], 
    help="Ensure the medication name is clearly visible in the image"
)

# Move the if block after uploaded_file is defined
if uploaded_file is not None:
    try:
        image = Image.open(uploaded_file)
        st.image(image, caption="Uploaded Image", use_container_width=True)
        
        # Perform OCR with error handling
        with st.spinner("🔍 Analyzing image..."):
            try:
                image_np = np.array(image)
                result = ocr_model.ocr(image_np)
                
                if not result or not result[0]:
                    st.warning("⚠️ No text detected in the image. Please upload a clear image of medication text.")
                    st.stop()
                
                texts = []
                for res in result[0]:
                    if res and res[1] and len(res[1]) > 0:
                        text = res[1][0].strip()
                        if len(text) > 1:
                            texts.append(text)
                            
                full_ocr_text = " ".join(texts)
                
                if not full_ocr_text:
                    st.warning("⚠️ No readable text found in the image. Please try with a clearer image.")
                    st.stop()
                    
            except Exception as ocr_error:
                st.error("🚨 Failed to process image with OCR")
                st.text(f"OCR Error: {str(ocr_error)}")
                st.stop()
        
        st.subheader("Extracted Text")
        st.code(full_ocr_text)

        # Match results
        if medications:
            with st.spinner("🔬 Matching against database..."):
                matched_results = check_drug_name_in_db(full_ocr_text, medications)
            
            if matched_results:
                # Display top 3 matches
                top_matches = matched_results[:3]
                st.subheader("Top 3 Matches")
                cols = st.columns(3)
                for idx, (name, score, med) in enumerate(top_matches):
                    with cols[idx]:
                        display_med_card(med, score, idx)

                # Button to show all matches
                if len(matched_results) > 3:
                    if st.button("🔍 See All Matches"):
                        display_all_matches(matched_results)

                # Preview modal for selected medication
                if st.session_state.show_preview:
                    with st.container(border=True):
                        st.subheader("Selected Medication Preview")
                        display_med_details(st.session_state.selected_med)
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✅ Confirm Selection", type="primary", key="confirm_btn"):
                                st.success("Selection confirmed!")
                                st.balloons()
                        with col2:
                            if st.button("❌ Cancel"):
                                st.session_state.show_preview = False
                                st.session_state.selected_med = None
            else:
                st.warning("⚠️ No matches found in the database")
    except Exception as e:
        st.error("🚨 An error occurred while processing the image")
        st.text(traceback.format_exc())