"""Catalogue des modèles Pliage et `model_item` OpenWebUI (repris de app.py)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from pliage.config import USER_NAME

PREMIUM_ID = "pliage-premium"

MODELS = {
    "⭐ Le Premium": {
        "id": PREMIUM_ID,
        "type": "openwebui",
        "description": "Premium + outils + recherche documentaire",
    },
    "🔎 Recherche documentaire": {
        "id": "pliage_recherche_documentaire",
        "type": "openwebui",
        "description": "Pipe de recherche documentaire",
    },
    "📚 Recherche documentaire classique": {
        "id": "pliage_recherche_documentaire_classique",
        "type": "openwebui",
        "description": "Recherche documentaire classique",
    },
    "🧠 Le Cerveau": {
        "id": "openai/gpt-oss-120b",
        "type": "standard",
        "description": "GPT OSS 120B",
    },
    "💬 Le Généraliste": {
        "id": "mistral-medium-2508",
        "type": "standard",
        "description": "Mistral Medium",
    },
    "🌱 Le Frugal": {
        "id": "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        "type": "standard",
        "description": "Mistral Small",
    },
    "💻 Le Développeur": {
        "id": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
        "type": "standard",
        "description": "Qwen3 Coder",
    },
}


def get_variables():
    now = datetime.now(ZoneInfo("Europe/Paris"))
    return {
        "{{USER_NAME}}": USER_NAME,
        "{{USER_LOCATION}}": "Unknown",
        "{{CURRENT_DATETIME}}": now.strftime("%Y-%m-%d %H:%M:%S"),
        "{{CURRENT_DATE}}": now.strftime("%Y-%m-%d"),
        "{{CURRENT_TIME}}": now.strftime("%H:%M:%S"),
        "{{CURRENT_WEEKDAY}}": now.strftime("%A"),
        "{{CURRENT_TIMEZONE}}": "Europe/Paris",
        "{{USER_LANGUAGE}}": "fr-FR",
    }


def premium_model_item():
    return {
        "id": "pliage-premium",
        "name": "[PLIAGE] Le Premium",
        "object": "model",
        "owned_by": "openai",
        "connection_type": "external",
        "preset": True,
        "info": {
            "id": "pliage-premium",
            "base_model_id": "openai/gpt-oss-120b",
            "name": "[PLIAGE] Le Premium",
            "meta": {
                "capabilities": {
                    "file_context": True,
                    "vision": True,
                    "file_upload": True,
                    "web_search": False,
                    "image_generation": False,
                    "code_interpreter": True,
                    "terminal": True,
                    "citations": True,
                    "status_updates": True,
                    "builtin_tools": True,
                    "usage": True,
                },
                "builtinTools": {
                    "time": True,
                    "memory": True,
                    "chats": True,
                    "notes": False,
                    "knowledge": True,
                    "channels": False,
                    "web_search": False,
                    "image_generation": False,
                    "code_interpreter": True,
                },
                "defaultFeatureIds": ["code_interpreter"],
                "toolIds": ["server:mcp:data-gouv-mcp"],
            },
        },
        "actions": [],
        "filters": [],
        "tags": [],
    }


def _pipe_model_item(model_id, name, with_hidden_flag):
    meta = {
        "capabilities": {
            "file_context": True,
            "vision": True,
            "file_upload": True,
            "web_search": True,
            "image_generation": False,
            "code_interpreter": True,
            "terminal": True,
            "citations": True,
            "status_updates": True,
            "builtin_tools": False,
            "usage": True,
        },
        "builtinTools": {},
        "defaultFeatureIds": [],
    }
    if with_hidden_flag:
        meta["hidden"] = False
    return {
        "id": model_id,
        "name": name,
        "object": "model",
        "owned_by": "openai",
        "pipe": {"type": "pipe"},
        "has_user_valves": False,
        "info": {
            "id": model_id,
            "base_model_id": None,
            "name": name,
            "meta": meta,
        },
        "actions": [],
        "filters": [],
        "tags": [],
    }


def get_model_item(model_id, display_name):
    if model_id == PREMIUM_ID:
        return premium_model_item()
    if model_id == "pliage_recherche_documentaire":
        return _pipe_model_item(
            model_id, "[PLIAGE] Recherche Documentaire", True
        )
    if model_id == "pliage_recherche_documentaire_classique":
        return _pipe_model_item(
            model_id, "[PLIAGE] Recherche documentaire (Classique)", False
        )
    return {"id": model_id, "name": display_name, "object": "model"}
