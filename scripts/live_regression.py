"""Test de régression réel contre Pliage (à lancer depuis le réseau du travail).

    py -3.12 scripts/live_regression.py

Affiche le tableau produit par le pipeline multi-RAG. Aucune valeur attendue
n'est codée ici : comparez manuellement avec le document.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pliage.client import Conversation, PliageClient  # noqa: E402
from pliage.config import API_TOKEN, setup_logging  # noqa: E402
from rag.pipeline import run_multi_rag  # noqa: E402
from rag.router import route  # noqa: E402
from ui.render import build_answer_markdown  # noqa: E402

QUESTION = """Dans MAISON DE SANTE DU PAYS ACADIEN.pdf, donne-moi :

- nombre de PS et type
- patientèle de référence
- file active
- nombre de patients C2S et taux
- nombre de patients AME et taux
- rémunération allouée dans le cadre de l'ACI
- horaires d'ouverture
- accès aux soins non programmés chaque jour ouvré et lien avec le SAS
- existence de protocoles de coopération pluriprofessionnels
- présence d'IPA
- formation des jeunes : accueil en stage
- système d'information labellisé par l'ANS"""


def main():
    setup_logging()
    question = sys.argv[1] if len(sys.argv) > 1 else QUESTION
    report = run_multi_rag(PliageClient(API_TOKEN), Conversation(), route(question), on_step=print)
    print()
    print(build_answer_markdown(report))
    print()
    for check in report.checks:
        print(("OK  " if check.ok else "KO  ") + check.message)


if __name__ == "__main__":
    main()
