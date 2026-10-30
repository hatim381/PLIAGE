"""Assistant Pliage — interface Streamlit.

Logique métier dans :
- pliage/  : client HTTP OpenWebUI, modèles, parser des tool calls
- rag/     : multi-RAG (routage, recherches ciblées, extraction, validation)
- ui/      : rendu du tableau, des preuves et du debug
"""

import json
import logging
import os

import streamlit as st

from pliage.client import Conversation, PliageClient
from pliage.config import API_TOKEN, setup_logging
from pliage.errors import PliageError
from pliage.models import MODELS, PREMIUM_ID
from pliage.parser import (
    clean_final_answer,
    extract_knowledge_results,
    extract_source_names_fallback,
    extract_sources,
    extract_tool_calls,
)
from pliage.prompts import STRICT_RAG_INSTRUCTIONS
from rag.pipeline import run_multi_rag
from rag.router import route
from ui.render import build_answer_markdown, render_debug, render_evidence, render_diagnostics

setup_logging()
log = logging.getLogger("app")

st.set_page_config(page_title="Assistant Pliage", page_icon="🧠", layout="wide")


# ============================================================
# SESSION
# ============================================================

DEFAULT_STATE = {
    "messages": [],
    "conversation": None,
    "last_debug": {},
    "selected_model_id": None,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value

if st.session_state.conversation is None:
    st.session_state.conversation = Conversation()


def reset_conversation():
    st.session_state.messages = []
    # Pas de chat_id inventé : Pliage le crée au premier message.
    st.session_state.conversation = Conversation()
    st.session_state.last_debug = {}


if not API_TOKEN:
    st.error("API_TOKEN absent du fichier .env")
    st.code("API_TOKEN=TA_CLE_API_PLIAGE", language="text")
    st.stop()

client = PliageClient(API_TOKEN)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.title("🧠 Pliage")

    selected_name = st.selectbox("Modèle", list(MODELS.keys()), index=0)
    MODEL = MODELS[selected_name]
    selected_model = MODEL["id"]
    is_premium = selected_model == PREMIUM_ID

    if (
        st.session_state.selected_model_id
        and st.session_state.selected_model_id != selected_model
    ):
        reset_conversation()
    st.session_state.selected_model_id = selected_model

    st.caption(MODEL["description"])
    st.divider()

    strict_rag = st.toggle(
        "🛡️ Extraction documentaire stricte",
        value=True,
        help="Distingue les données réelles des valeurs méthodologiques "
             "et n'invente pas les informations manquantes.",
    )
    multi_rag = st.toggle(
        "🔎 Recherche multi-RAG",
        value=True,
        disabled=not is_premium,
        help="Décompose une demande d'indicateurs en recherches ciblées, "
             "puis consolide et valide les valeurs en Python (⭐ Le Premium).",
    )
    visual_review = None
    with st.expander("Relire les tableaux en image"):
        st.caption("Garde Le Premium pour la recherche documentaire. Le modèle ci-dessous lit les images. Joins le PDF ciblé et indique les pages à relire. Les images seront envoyées à ton API Pliage avec le modèle choisi.")
        visual_pdf = st.file_uploader("PDF pour la relecture visuelle", type=["pdf"])
        visual_pages = st.text_input("Pages à relire (maximum 5)", placeholder="3,7")
        visual_model = st.text_input("Identifiant du modèle Pliage compatible images", value=os.getenv("VISION_MODEL", ""))
        visual_enabled = st.checkbox("Activer la relecture visuelle pour cette recherche")
        if visual_enabled and visual_pdf and visual_model.strip() and visual_pages.strip():
            visual_review = {"data": visual_pdf.getvalue(), "name": visual_pdf.name,
                             "pages": visual_pages, "model": visual_model}
        elif visual_enabled:
            st.info("Renseigne le PDF, les pages et le modèle avant la recherche.")
    show_evidence = st.toggle("📑 Afficher les preuves", value=False)
    debug_mode = st.toggle("🛠️ Debug", value=False)

    st.divider()
    if st.button("🗑️ Nouvelle conversation", use_container_width=True):
        reset_conversation()
        st.rerun()

    st.divider()
    if strict_rag:
        st.success("🛡️ Contrôle documentaire actif")
    if st.session_state.conversation.chat_id:
        st.caption("🟢 Conversation Pliage active")
    st.caption(f"{len(st.session_state.messages)} message(s)")

conversation = st.session_state.conversation

st.title("🧠 Assistant Pliage")
st.caption(f"Modèle actif : **{selected_name}**")
if strict_rag and MODEL["type"] == "openwebui":
    st.caption(
        "🛡️ Extraction stricte : pas de valeur inventée, contrôle des tableaux "
        "et des données établissement."
    )


# ============================================================
# PROMPT
# ============================================================

def prepare_prompt(user_prompt):
    # Instructions strictes pour le Premium, qui exécute query_knowledge_files.
    if strict_rag and is_premium:
        return (
            STRICT_RAG_INSTRUCTIONS
            + "\n\n============================================\n"
            + "QUESTION DE L'UTILISATEUR\n"
            + "============================================\n\n"
            + user_prompt
        )
    return user_prompt


# ============================================================
# AFFICHAGE
# ============================================================

def display_sources(sources, title="### 📚 Documents consultés"):
    if not sources:
        return
    st.markdown("---")
    st.markdown(title)
    for source in sources:
        st.markdown(f"- 📄 **{source}**")


def display_documentary_json(parsed):
    query = parsed.get("user_query")
    results = parsed.get("results", [])
    if query:
        st.caption(f"🔎 Recherche : {query}")
    st.write(f"**{parsed.get('count', 0)} résultat(s)**")
    if not results:
        st.info("Aucun résultat documentaire trouvé.")
        return

    sources = []
    for result in results:
        if isinstance(result, dict) and result.get("source") and result["source"] not in sources:
            sources.append(result["source"])
    display_sources(sources, "### 📚 Documents trouvés")

    with st.expander("Afficher les passages retrouvés"):
        for index, result in enumerate(results, start=1):
            if not isinstance(result, dict):
                continue
            st.markdown(f"#### {result.get('source') or f'Résultat {index}'}")
            text = result.get("content", "")
            st.markdown(text[:4000] + ("\n\n…" if len(text) > 4000 else ""))


def display_pliage_content(content):
    try:
        parsed = json.loads(content)
    except ValueError:
        parsed = None

    if isinstance(parsed, dict):
        if "user_query" in parsed and "results" in parsed:
            display_documentary_json(parsed)
        else:
            st.json(parsed)
        return

    passages = extract_knowledge_results(content)
    sources = [s.name for s in extract_sources(passages)] or extract_source_names_fallback(content)
    cleaned = clean_final_answer(content)

    if cleaned:
        st.markdown(cleaned)
    else:
        st.info("Les outils ont été exécutés, mais aucune réponse finale n'a été produite.")
    display_sources(sources)

    if show_evidence and passages:
        with st.expander(f"📑 Passages documentaires ({len(passages)})"):
            for p in passages:
                st.markdown(f"**{p.source or 'source inconnue'}** — requête : `{p.query or '—'}`")
                st.text(p.content[:1500] + ("…" if len(p.content) > 1500 else ""))


def display_assistant_message(message):
    report = message.get("report")
    if report is not None:
        st.markdown(report.markdown)
        render_diagnostics(report)
        if show_evidence:
            render_evidence(report)
        return
    if not message.get("content"):
        st.warning("Aucun contenu retourné.")
        return
    display_pliage_content(message["content"])


def show_error(error):
    if isinstance(error, PliageError):
        st.error(f"**{error.user_title}** — {error}")
    else:
        log.exception("erreur inattendue")
        st.error(f"**Erreur inattendue** — {type(error).__name__} : {error}")


# ============================================================
# APPELS
# ============================================================

def standard_history():
    messages = []
    for message in st.session_state.messages:
        content = message["content"]
        if message["role"] == "assistant" and message.get("report") is None:
            content = clean_final_answer(content)
        messages.append({"role": message["role"], "content": content})
    return messages


def debug_for_turn(turn):
    calls = extract_tool_calls(turn.content)
    passages = extract_knowledge_results(turn.message)
    return {
        "mode": "openwebui",
        "chat_id": turn.chat_id,
        "task_ids": turn.task_ids,
        "assistant_message_id": turn.assistant_message_id,
        "message_match": turn.match_method,
        "done": turn.message.get("done"),
        "model": turn.model,
        "elapsed_s": round(turn.elapsed, 1),
        "content_length": len(turn.content),
        "tool_calls": [{"name": c.name, "query": c.query} for c in calls],
        "knowledge_chunks": len(passages),
        "sources": [s.name for s in extract_sources(passages)],
    }


def run_multi_rag_turn(plan):
    with st.status("🔎 Analyse de la demande...", expanded=True) as status:
        def on_step(label):
            status.update(label=label)
            status.write(label)

        try:
            report = run_multi_rag(client, conversation, plan, on_step=on_step, visual_review=visual_review)
        except Exception:
            status.update(label="❌ Analyse interrompue", state="error", expanded=False)
            raise
        on_step("📝 Construction de la réponse…")
        report.markdown = build_answer_markdown(report)
        status.update(label="✅ Analyse terminée", state="complete", expanded=False)

    report.debug["mode"] = "multi-rag"
    report.debug["queries"] = [
        {"family": q.family, "attempt": q.attempt, "text": q.text} for q in report.queries
    ]
    st.session_state.last_debug = report.debug
    return {"role": "assistant", "content": report.markdown, "report": report}


def run_openwebui_turn(prompt):
    placeholder = st.empty()
    placeholder.info(
        "🔎 Recherche et vérification des données documentaires..."
        if strict_rag else f"{selected_name} travaille..."
    )
    try:
        turn = client.run_openwebui_turn(
            prepare_prompt(prompt), conversation, selected_model, selected_name
        )
    finally:
        placeholder.empty()
    st.session_state.last_debug = debug_for_turn(turn)
    return {"role": "assistant", "content": turn.content}


def run_standard_turn():
    placeholder = st.empty()
    placeholder.info(f"{selected_name} travaille...")
    try:
        content = client.call_standard(standard_history(), selected_model)
    finally:
        placeholder.empty()
    st.session_state.last_debug = {
        "mode": "standard", "model": selected_model, "content_length": len(content),
    }
    return {"role": "assistant", "content": content}


def answer(prompt):
    if MODEL["type"] != "openwebui":
        return run_standard_turn()
    if is_premium and multi_rag:
        plan = route(prompt)
        if plan.is_extraction:
            return run_multi_rag_turn(plan)
    return run_openwebui_turn(prompt)


# ============================================================
# HISTORIQUE + SAISIE
# ============================================================

for message in st.session_state.messages:
    role = message["role"]
    with st.chat_message(role, avatar="👤" if role == "user" else "🧠"):
        if role == "assistant":
            display_assistant_message(message)
        else:
            st.markdown(message["content"])

prompt = st.chat_input("Pose ta question à Pliage...")

if prompt:
    # Seule la vraie question de l'utilisateur est affichée.
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="🧠"):
        try:
            reply = answer(prompt)
            display_assistant_message(reply)
            st.session_state.messages.append(reply)
        except Exception as error:  # toujours affichée, jamais ignorée
            show_error(error)


if debug_mode and st.session_state.last_debug:
    render_debug(st.session_state.last_debug)
