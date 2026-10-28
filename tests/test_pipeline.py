"""Test de bout en bout du pipeline avec un faux client Pliage.

Les passages sont synthétiques ; ils imitent la forme des documents ACI
(tableau + note méthodologique) pour vérifier le comportement, et ne
servent pas de source cachée : l'application ne contient aucune valeur.
"""

import json
import pathlib
import re

from conftest import tool_block
from pliage.client import Conversation, PliageTurn
from pliage.parser import extract_knowledge_results
from rag.pipeline import run_multi_rag
from rag.retrieval import EvidenceStore, build_queries, source_matches_target
from rag.router import route
from rag.schemas import CONFIRMED, NOT_FOUND
from ui.render import build_answer_markdown

TARGET = "MSP TEST DU VAL.pdf"
CHUNKS = [
    {"content": "Patientèle de référence : 2 133 | File active : 2 191 | Patients C2S : 140 (6 %)",
     "source": TARGET, "file_id": "f-target"},
    {"content": "Rémunération calculée pour une patientèle de référence de 4 000 patients.",
     "source": TARGET, "file_id": "f-target"},
    {"content": "Patientèle de référence : 9 876", "source": "AUTRE MSP.pdf", "file_id": "f-other"},
]


class FakeClient:
    def __init__(self, extraction):
        self.extraction = extraction
        self.turns = 0
        self.prompts = []

    def run_openwebui_turn(self, prompt, conv, model_id, model_name, timeout=240):
        self.turns += 1
        self.prompts.append(prompt)
        # Le faux Premium exécute chaque requête demandée ; seule celle de la
        # patientèle ramène les passages.
        content = ""
        for query in re.findall(r"^\d+\. (.+)$", prompt, flags=re.MULTILINE):
            chunks = CHUNKS if "patientèle" in query else []
            content += tool_block("query_knowledge_files", {"query": query}, chunks)
        content += "RECHERCHES TERMINÉES"
        conv.chat_id = conv.chat_id or "chat-1"
        conv.parent_id = f"assistant-{self.turns}"
        message = {"id": conv.parent_id, "content": content, "done": True, "role": "assistant"}
        return PliageTurn(content=content, message=message, chat_id=conv.chat_id,
                          assistant_message_id=conv.parent_id, match_method="expected_id")

    def call_standard(self, messages, model_id, timeout=300):
        return json.dumps({"candidates": self.extraction})


def test_end_to_end_patientele():
    question = f"Dans {TARGET}, donne la patientèle de référence, la file active et le nombre de patients AME"
    plan = route(question)
    assert plan.target_file == TARGET
    assert plan.families == ["patientele"]

    client = FakeClient([
        {"indicator": "patientele_reference", "value": 2133, "chunk": "C1",
         "quote": "Patientèle de référence : 2 133", "value_type": "establishment_value"},
        # le LLM se trompe sur le type : le contexte doit le corriger
        {"indicator": "patientele_reference", "value": 4000, "chunk": "C2",
         "quote": "pour une patientèle de référence de 4 000 patients", "value_type": "establishment_value"},
        {"indicator": "file_active", "value": 2191, "chunk": "C1",
         "quote": "File active : 2 191", "value_type": "establishment_value"},
    ])
    conv = Conversation()
    steps = []
    report = run_multi_rag(client, conv, plan, on_step=steps.append)

    assert conv.chat_id == "chat-1"
    assert conv.parent_id == f"assistant-{client.turns}"
    assert all(e.source == TARGET for e in report.evidence)  # autre MSP écartée
    assert report.debug["discarded"]["other_file"] >= 1

    results = report.results
    assert results["patientele_reference"].status == CONFIRMED
    assert results["patientele_reference"].value == 2133
    assert results["file_active"].value == 2191
    assert results["ame_patients"].status == NOT_FOUND
    assert results["ame_patients"].value is None

    markdown = build_answer_markdown(report)
    assert "| Patientèle de référence | 2 133 | ✅ Confirmé |" in markdown
    assert "Non trouvé" in markdown
    assert "9 876" not in markdown
    assert any(s.startswith("🔎 Analyse") for s in steps)


def test_no_retry_when_family_found():
    plan = route(f"Dans {TARGET}, quelle est la patientèle de référence ?")
    client = FakeClient([])
    run_multi_rag(client, Conversation(), plan)
    assert client.turns == 1


def test_retry_is_bounded():
    plan = route(f"Dans {TARGET}, présence d'IPA ?")
    client = FakeClient([])
    report = run_multi_rag(client, Conversation(), plan)  # aucun passage IPA dans CHUNKS
    assert client.turns == 3
    assert [q.attempt for q in report.queries] == [1, 2, 3]
    assert report.results["presence_ipa"].status == NOT_FOUND


def test_keywords_alone_do_not_cover_a_family():
    # Le passage contient « rémunération » mais n'a été ramené que par la requête patientèle.
    plan = route(f"Dans {TARGET}, patientèle et rémunération ACI ?")
    store = EvidenceStore(plan)
    planned = build_queries(plan, plan.families, 1)
    chunk = {"content": "Patientèle 2 133 ; rémunération détaillée plus loin", "source": TARGET}
    content = tool_block("query_knowledge_files", {"query": planned[0].text}, [chunk])
    store.add_results(extract_knowledge_results(content), planned)
    assert store.missing_families(plan.families) == ["remuneration"]


def test_queries_keep_target_and_years():
    plan = route(f"Dans {TARGET}, rémunération ACI 2023 et avance 2024")
    [query] = build_queries(plan, ["remuneration"], 1)
    assert "MSP TEST DU VAL" in query.text and "2023 2024" in query.text


def test_source_matching():
    assert source_matches_target("MSP_TEST_DU_VAL.pdf", TARGET)
    assert not source_matches_target("AUTRE MSP.pdf", TARGET)
    assert not source_matches_target(None, TARGET)
    assert source_matches_target("n'importe.pdf", None)


def test_store_without_target_keeps_everything():
    plan = route("Quelle est la patientèle de référence ?")
    store = EvidenceStore(plan)
    content = tool_block("query_knowledge_files", {"query": "q"}, CHUNKS)
    store.add_results(extract_knowledge_results(content), [])
    assert len(store.items) == 3


def test_no_establishment_name_or_value_is_hardcoded():
    root = pathlib.Path(__file__).resolve().parent.parent
    files = [p for d in ("pliage", "rag", "ui") for p in (root / d).glob("*.py")] + [root / "app.py"]
    code = "".join(p.read_text(encoding="utf-8") for p in files)
    for forbidden in ("ACADIEN", "2 133", "2133", "52 110", "21 513", "30 596", "23 687"):
        assert forbidden not in code
