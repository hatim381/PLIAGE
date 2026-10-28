"""Configuration : URL Pliage, token (.env), logging sans secret."""

import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")

BASE_URL = os.getenv(
    "PLIAGE_BASE_URL",
    "https://openwebui.pliage-prod.socle-ia."
    "data-ia.prod.atlas.fabrique.social.gouv.fr",
).rstrip("/")

COMPLETIONS_URL = f"{BASE_URL}/api/chat/completions"

# Modèle sans outils utilisé pour l'étape d'extraction structurée.
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "openai/gpt-oss-120b")

USER_NAME = os.getenv("PLIAGE_USER_NAME", "Hatim")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9._\-]{6,}")


def redact(text):
    """Masque toute trace de token dans une chaîne."""
    if not isinstance(text, str):
        return text
    if API_TOKEN:
        text = text.replace(API_TOKEN, "***")
    text = _BEARER_RE.sub(r"\1***", text)
    return _SK_RE.sub("sk-***", text)


class RedactingFilter(logging.Filter):
    """Filet de sécurité : aucun token ne sort dans les logs."""

    def filter(self, record):
        record.msg = redact(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def setup_logging():
    if getattr(setup_logging, "_done", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(levelname)s %(name)s - %(message)s")
    )
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(LOG_LEVEL)
    # urllib3 en DEBUG afficherait les URL et en-têtes.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    setup_logging._done = True
