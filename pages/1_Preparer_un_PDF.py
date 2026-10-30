"""Prepare scan orientation before importing the PDF into Pliage."""

import hashlib
from pathlib import Path

import streamlit as st

from pliage.pdf_preparation import detect_rotation, open_pdf, preview_page, rotate_pdf

st.set_page_config(page_title="Préparer un PDF", page_icon="📄", layout="wide")
st.title("Préparer un PDF avant l’OCR")
st.write(
    "Remets les pages dans le bon sens, puis télécharge le PDF corrigé. "
    "Réimporte ce fichier dans Pliage pour relancer son indexation."
)
st.caption(
    "Le traitement se déroule sur la machine exécutant Streamlit. "
    "Cet écran ne lance pas l’OCR et ne modifie pas les documents déjà indexés."
)
upload = st.file_uploader("PDF à préparer", type=["pdf"])
if upload is None:
    st.stop()
data = upload.getvalue()
identity = hashlib.sha256(data).hexdigest()
try:
    with open_pdf(data) as document:
        page_count = len(document)
except Exception:
    st.error("PDF illisible, protégé ou dépassant 100 pages.")
    st.stop()

if st.session_state.get("pdf_identity") != identity:
    st.session_state.pdf_identity = identity
    st.session_state.pdf_corrections = {}
corrections = st.session_state.pdf_corrections

if st.button("Proposer les rotations automatiquement"):
    progress = st.progress(0)
    unresolved = []
    try:
        suggestions = {}
        for index in range(page_count):
            rotation = detect_rotation(data, index)
            if rotation is None:
                unresolved.append(index + 1)
            else:
                suggestions[index] = rotation
            progress.progress((index + 1) / page_count)
        corrections.update(suggestions)
        for page_index, rotation in suggestions.items():
            st.session_state[f"rotation-{identity}-{page_index}"] = rotation
        st.success("Suggestions appliquées. Vérifie les pages avant le téléchargement.")
        if unresolved:
            st.info("Orientation indéterminée, correction existante conservée : " + ", ".join(map(str, unresolved)))
    except (RuntimeError, OSError) as error:
        st.info(str(error))
    except Exception:
        st.error("Détection interrompue. Utilise les corrections manuelles.")
    finally:
        progress.empty()

page_number = st.selectbox("Page à vérifier", range(1, page_count + 1))
index = page_number - 1
rotation_key = f"rotation-{identity}-{index}"
if rotation_key not in st.session_state:
    st.session_state[rotation_key] = corrections.get(index, 0)
angle = st.selectbox(
    "Rotation supplémentaire dans le sens horaire",
    [0, 90, 180, 270],
    key=rotation_key,
)
corrections[index] = angle
left, right = st.columns(2)
with left:
    st.caption("Original")
    st.image(preview_page(data, index), use_container_width=True)
with right:
    st.caption("Après correction")
    st.image(preview_page(data, index, angle), use_container_width=True)

changed = {page + 1: degrees for page, degrees in corrections.items() if degrees}
st.write("Pages corrigées : " + (", ".join(f"{page} (+{degrees}°)" for page, degrees in sorted(changed.items())) or "aucune"))
st.download_button(
    "Télécharger le PDF corrigé",
    data=rotate_pdf(data, corrections),
    file_name=Path(upload.name).stem + "_redresse.pdf",
    mime="application/pdf",
)
st.info("Après réimport dans Pliage, vérifie les effectifs, les colonnes des tableaux et les valeurs extraites.")
