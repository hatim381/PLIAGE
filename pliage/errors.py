"""Erreurs typées de l'intégration Pliage.

Chaque classe porte un message lisible, affichable tel quel dans Streamlit.
Aucun message ne contient le token ni les en-têtes HTTP.
"""


class PliageError(Exception):
    """Erreur générique Pliage."""

    user_title = "Erreur Pliage"


class PliageConfigError(PliageError):
    user_title = "Configuration manquante"


class VisualReviewConfigError(PliageConfigError, ValueError):
    user_title = "Relecture visuelle non lancée"


class PliageNetworkError(PliageError):
    user_title = "Erreur réseau"


class PliageTimeoutError(PliageError):
    user_title = "Délai dépassé"


class PliageHTTPError(PliageError):
    user_title = "Erreur HTTP Pliage"

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class PliageTaskError(PliageError):
    user_title = "Tâche Pliage échouée"


class PliageInvalidResponseError(PliageError):
    user_title = "Réponse Pliage invalide"


class ExtractionParseError(PliageError):
    user_title = "Analyse de la réponse impossible"
