import json

from conftest import pliage_tool_block, tool_block
from pliage.client import find_assistant_message
from pliage.parser import (
    clean_final_answer,
    decode_nested_json,
    extract_knowledge_results,
    extract_source_names_fallback,
    extract_sources,
    extract_tool_calls,
    parse_openwebui_details,
)

CHUNK_A = {"content": "Patientèle de référence : 2 133", "source": "MSP A.pdf", "file_id": "f-a", "distance": 0.21}
CHUNK_B = {"content": "File active : 2 191", "source": "MSP B.pdf", "file_id": "f-b"}


def test_decode_simple_json():
    assert decode_nested_json('{"a": 1}') == {"a": 1}


def test_decode_double_encoded_json():
    twice = json.dumps(json.dumps([CHUNK_A]))
    assert decode_nested_json(twice) == [CHUNK_A]


def test_decode_depth_limit_and_invalid():
    deep = "[1]"
    for _ in range(10):
        deep = json.dumps(deep)
    assert isinstance(decode_nested_json(deep, max_depth=3), str)
    assert decode_nested_json("pas du json") == "pas du json"


def test_tool_call_block_is_parsed():
    content = tool_block("query_knowledge_files", {"query": "patientèle"}, [CHUNK_A])
    calls = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0].name == "query_knowledge_files"
    assert calls[0].query == "patientèle"
    assert calls[0].result == [CHUNK_A]


def test_pliage_format_result_in_block_body():
    content = (
        '<details type="reasoning" done="true" duration="1"><summary>Thought</summary>x</details>\n'
        + pliage_tool_block("query_knowledge_files", {"query": "patientèle référence"}, [CHUNK_A, CHUNK_B])
        + "RECHERCHES TERMINÉES"
    )
    [call] = extract_tool_calls(content)
    assert call.query == "patientèle référence"
    results = extract_knowledge_results(content)
    assert [r.source for r in results] == ["MSP A.pdf", "MSP B.pdf"]
    assert results[0].content == "Patientèle de référence : 2 133"
    assert clean_final_answer(content) == "RECHERCHES TERMINÉES"


def test_knowledge_results_keep_provenance():
    content = tool_block("query_knowledge_files", {"query": "patientèle"}, [CHUNK_A])
    [result] = extract_knowledge_results(content)
    assert result.content == "Patientèle de référence : 2 133"
    assert result.source == "MSP A.pdf"
    assert result.file_id == "f-a"
    assert result.distance == 0.21
    assert result.query == "patientèle"
    assert result.page is None  # jamais inventée


def test_multiple_files_and_duplicates():
    content = (
        tool_block("query_knowledge_files", {"query": "q1"}, [CHUNK_A, CHUNK_B])
        + tool_block("query_knowledge_files", {"query": "q2"}, [CHUNK_A])
    )
    results = extract_knowledge_results(content)
    assert len(results) == 2
    merged = next(r for r in results if r.source == "MSP A.pdf")
    assert merged.query == "q1 || q2"
    assert [s.name for s in extract_sources(results)] == ["MSP A.pdf", "MSP B.pdf"]


def test_html_entities_in_content():
    chunk = {"content": "Protocoles &amp; coordination : l&#x27;équipe", "source": "x.pdf"}
    content = tool_block("query_knowledge_files", {"query": "q"}, [chunk])
    [result] = extract_knowledge_results(content)
    assert result.content == "Protocoles & coordination : l'équipe"


def test_single_encoded_and_wrapped_results():
    content = tool_block("query_knowledge_files", {"query": "q"}, {"results": [CHUNK_B]}, encode_times=1)
    [result] = extract_knowledge_results(content)
    assert result.source == "MSP B.pdf"


def test_non_knowledge_tools_are_ignored():
    content = tool_block("get_current_time", {}, {"content": "12:00"})
    assert extract_knowledge_results(content) == []


def test_message_sources_column_format():
    message = {
        "content": "Réponse",
        "sources": [{
            "source": {"name": "MSP C.pdf", "id": "f-c"},
            "document": ["Stages : 4"],
            "metadata": [{"file_id": "f-c", "name": "MSP C.pdf", "page": 3}],
            "distances": [0.4],
        }],
    }
    [result] = extract_knowledge_results(message)
    assert (result.source, result.file_id, result.page, result.distance) == ("MSP C.pdf", "f-c", "3", 0.4)


def test_clean_final_answer_removes_technical_blocks():
    content = (
        '<details type="reasoning" done="true"><summary>Thought</summary>raisonnement</details>\n'
        + tool_block("query_knowledge_files", {"query": "q"}, [CHUNK_A])
        + "**Réponse** finale"
    )
    assert clean_final_answer(content) == "**Réponse** finale"


def test_nested_and_unclosed_details():
    content = "<details type='a'>x<details>y</details>z</details>ok<details type=\"tool_calls\">tronqué"
    blocks = parse_openwebui_details(content)
    assert [b.type for b in blocks] == ["a", "tool_calls"]
    assert clean_final_answer(content) == "ok"


def test_legacy_source_fallback():
    assert extract_source_names_fallback('{"source": "doc.pdf"}') == ["doc.pdf"]


def test_find_assistant_message_fallbacks():
    history = {
        "currentId": "srv-2",
        "messages": {
            "u1": {"id": "u1", "role": "user"},
            "srv-2": {"id": "srv-2", "role": "assistant", "parentId": "u1", "timestamp": 2},
            "old": {"id": "old", "role": "assistant", "parentId": "u0", "timestamp": 1},
        },
    }
    assert find_assistant_message(history, "srv-2")[1] == "expected_id"
    msg, method = find_assistant_message(history, "generated-id", "u1")
    assert (msg["id"], method) == ("srv-2", "parent_of_user_message")
    msg, method = find_assistant_message(history, "generated-id", "unknown", previous_parent_id="old")
    assert (msg["id"], method) == ("srv-2", "current_id")
    # currentId pointe encore sur la réponse du tour précédent : on attend.
    history["currentId"] = "old"
    assert find_assistant_message(history, "generated-id", "unknown", previous_parent_id="old") == (None, "")
