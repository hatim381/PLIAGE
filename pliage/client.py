"""Client HTTP Pliage / OpenWebUI.

Flux conservé de la version d'origine :

1. POST /api/chat/completions  -> {"status", "task_ids", "chat_id"}
   (premier message : chat_id = None, Pliage crée le chat)
2. GET  /api/tasks/chat/{chat_id}  (polling des tâches actives)
3. GET  /api/v1/chats/{chat_id}    -> data["chat"]["history"]
4. message assistant terminé (done == True) ; son id devient parent_id.

Le client ne dépend pas de Streamlit : l'état de conversation est passé
via `Conversation`.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import requests

from pliage.config import BASE_URL, COMPLETIONS_URL, redact
from pliage.errors import (
    PliageConfigError,
    PliageError,
    PliageHTTPError,
    PliageInvalidResponseError,
    PliageNetworkError,
    PliageTaskError,
    PliageTimeoutError,
)
from pliage.models import get_model_item, get_variables

log = logging.getLogger("pliage.client")

MAX_ERROR_BODY = 1500
MAX_CONSECUTIVE_POLL_ERRORS = 5


@dataclass
class Conversation:
    chat_id: str | None = None
    parent_id: str | None = None
    session_id: str | None = None

    def ensure_session_id(self):
        if not self.session_id:
            self.session_id = uuid.uuid4().hex[:20]
        return self.session_id


@dataclass
class PliageTurn:
    content: str
    message: dict
    chat_id: str
    task_ids: list = field(default_factory=list)
    assistant_message_id: str | None = None
    model: str | None = None
    elapsed: float = 0.0
    match_method: str = ""


# ------------------------------------------------------------
# Sélection du message assistant (fonction pure, testée)
# ------------------------------------------------------------

def find_assistant_message(
    history,
    expected_id=None,
    user_message_id=None,
    previous_parent_id=None,
):
    """Retrouve le message assistant de ce tour.

    Ordre : id attendu > enfant du message utilisateur > currentId
    (s'il ne s'agit pas de la réponse du tour précédent).
    Retourne (message, méthode) ou (None, "").
    """
    messages = history.get("messages") or {}
    if not isinstance(messages, dict):
        return None, ""

    def is_assistant(m):
        return isinstance(m, dict) and m.get("role") == "assistant"

    if expected_id and is_assistant(messages.get(expected_id)):
        return messages[expected_id], "expected_id"

    if user_message_id:
        children = [
            m for m in messages.values()
            if is_assistant(m) and m.get("parentId") == user_message_id
        ]
        if children:
            children.sort(key=lambda m: m.get("timestamp") or 0)
            return children[-1], "parent_of_user_message"

    current = messages.get(history.get("currentId"))
    if is_assistant(current) and current.get("id") != previous_parent_id:
        return current, "current_id"

    return None, ""


def _message_error(message):
    error = message.get("error")
    if not error:
        return None
    if isinstance(error, dict):
        return str(error.get("content") or error)
    return str(error)


class PliageClient:

    def __init__(self, token, base_url=BASE_URL, completions_url=COMPLETIONS_URL):
        if not token:
            raise PliageConfigError("API_TOKEN absent du fichier .env")
        self._token = token
        self.base_url = base_url
        self.completions_url = completions_url

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    def _request(self, method, url, timeout, **kwargs):
        try:
            response = requests.request(
                method, url, headers=self._headers(), timeout=timeout, **kwargs
            )
        except requests.Timeout as error:
            raise PliageTimeoutError(
                f"Pliage n'a pas répondu à temps ({timeout} s)."
            ) from error
        except requests.RequestException as error:
            raise PliageNetworkError(
                f"Impossible de joindre Pliage ({type(error).__name__})."
            ) from error

        if response.status_code != 200:
            body = redact(response.text[:MAX_ERROR_BODY])
            path = url.replace(self.base_url, "")
            raise PliageHTTPError(
                f"HTTP {response.status_code} sur {path}\n\n{body}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as error:
            raise PliageInvalidResponseError(
                "Réponse JSON invalide :\n\n"
                + redact(response.text[:MAX_ERROR_BODY])
            ) from error

    # --------------------------------------------------------
    # Payload OpenWebUI (identique à la version d'origine)
    # --------------------------------------------------------

    def build_openwebui_payload(self, prompt, conv, model_id, model_name):
        session_id = conv.ensure_session_id()
        user_message_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        parent_id = conv.parent_id

        user_message = {
            "id": user_message_id,
            "parentId": parent_id,
            "childrenIds": [assistant_message_id],
            "role": "user",
            "content": prompt,
            "timestamp": int(time.time()),
            "models": [model_id],
        }

        payload = {
            "stream": True,
            "model": model_id,
            "params": {},
            "tool_servers": [],
            "features": {
                "voice": False,
                "image_generation": False,
                "code_interpreter": False,
                "web_search": False,
            },
            "variables": get_variables(),
            "model_item": get_model_item(model_id, model_name),
            "session_id": session_id,
            "chat_id": conv.chat_id,
            "id": assistant_message_id,
            "parent_id": parent_id,
            "user_message": user_message,
            "background_tasks": {
                "title_generation": True,
                "tags_generation": True,
                "follow_up_generation": True,
            },
            "stream_options": {"include_usage": True},
        }
        return payload, user_message_id, assistant_message_id

    # --------------------------------------------------------
    # Endpoints
    # --------------------------------------------------------

    def start_task(self, payload):
        data = self._request("POST", self.completions_url, 300, json=payload)
        if not isinstance(data, dict) or not data.get("chat_id"):
            raise PliageInvalidResponseError(
                "Pliage n'a pas renvoyé de chat_id.\n\n"
                + redact(json.dumps(data, ensure_ascii=False)[:MAX_ERROR_BODY])
            )
        return data

    def get_active_tasks(self, chat_id):
        data = self._request(
            "GET", f"{self.base_url}/api/tasks/chat/{chat_id}", 30
        )
        task_ids = data.get("task_ids", []) if isinstance(data, dict) else []
        return task_ids if isinstance(task_ids, list) else []

    def get_chat(self, chat_id):
        data = self._request("GET", f"{self.base_url}/api/v1/chats/{chat_id}", 30)
        if not isinstance(data, dict):
            raise PliageInvalidResponseError("Conversation Pliage illisible.")
        return data

    # --------------------------------------------------------
    # Attente
    # --------------------------------------------------------

    def _poll_once(self, chat_id):
        tasks = self.get_active_tasks(chat_id)
        chat = self.get_chat(chat_id)
        history = (chat.get("chat") or {}).get("history") or {}
        return tasks, history

    def _is_transient(self, error):
        if isinstance(error, (PliageNetworkError, PliageTimeoutError)):
            return True
        return isinstance(error, PliageHTTPError) and (error.status_code or 0) >= 500

    def wait_for_final_answer(
        self,
        chat_id,
        expected_id,
        user_message_id,
        previous_parent_id,
        timeout=240,
    ):
        started = time.time()
        consecutive_errors = 0
        empty_done_since = None

        while True:
            if time.time() - started > timeout:
                raise PliageTimeoutError(
                    f"Pliage n'a pas terminé après {timeout} secondes."
                )

            try:
                tasks, history = self._poll_once(chat_id)
                consecutive_errors = 0
            except PliageError as error:
                if not self._is_transient(error):
                    raise
                consecutive_errors += 1
                log.warning("polling chat: %s (%d)", error, consecutive_errors)
                if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                    raise
                time.sleep(1.5)
                continue

            message, method = find_assistant_message(
                history, expected_id, user_message_id, previous_parent_id
            )

            if message:
                error = _message_error(message)
                if error:
                    raise PliageTaskError(redact(error[:MAX_ERROR_BODY]))

                content = message.get("content")
                if message.get("done") is True and isinstance(content, str):
                    if content.strip():
                        return message, method, time.time() - started
                    # Terminé mais vide : on laisse aux tâches le temps de finir.
                    empty_done_since = empty_done_since or time.time()
                    if not tasks and time.time() - empty_done_since > 10:
                        return message, method, time.time() - started

            time.sleep(0.75 if tasks else 0.4)

    # --------------------------------------------------------
    # Tours complets
    # --------------------------------------------------------

    def run_openwebui_turn(self, prompt, conv, model_id, model_name, timeout=240):
        """Envoie un message dans la conversation et met à jour chat_id / parent_id."""
        payload, user_id, assistant_id = self.build_openwebui_payload(
            prompt, conv, model_id, model_name
        )
        previous_parent = conv.parent_id
        data = self.start_task(payload)

        chat_id = data["chat_id"]
        conv.chat_id = chat_id
        log.info("pliage task started chat=%s tasks=%s", chat_id, data.get("task_ids"))

        message, method, elapsed = self.wait_for_final_answer(
            chat_id=chat_id,
            expected_id=assistant_id,
            user_message_id=user_id,
            previous_parent_id=previous_parent,
            timeout=timeout,
        )
        if method != "expected_id":
            log.warning("assistant message found via fallback: %s", method)

        conv.parent_id = message.get("id")
        return PliageTurn(
            content=message.get("content") or "",
            message=message,
            chat_id=chat_id,
            task_ids=data.get("task_ids") or [],
            assistant_message_id=message.get("id"),
            model=message.get("model"),
            elapsed=elapsed,
            match_method=method,
        )

    def call_standard(self, messages, model_id, timeout=300):
        payload = {"model": model_id, "messages": messages, "stream": False}
        data = self._request("POST", self.completions_url, timeout, json=payload)

        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise PliageInvalidResponseError("Aucune réponse retournée.")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if content:
            return content
        if message.get("tool_calls"):
            raise PliageError(
                "Le modèle souhaite utiliser un outil. "
                "Utilise ⭐ Le Premium pour les outils Pliage."
            )
        raise PliageInvalidResponseError("Réponse vide.")
