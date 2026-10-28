"""Parser des contenus OpenWebUI.

Le `content` d'un message assistant Premium contient des blocs :

    <details type="reasoning" ...> ... </details>
    <details type="tool_calls" name="query_knowledge_files"
             arguments="{&quot;query&quot;: ...}"
             result="&quot;[{\\&quot;content\\&quot;: ...}]&quot;"> ... </details>

Les attributs sont échappés HTML et le résultat est souvent du JSON
encodé plusieurs fois. On analyse la structure (et non une regex globale)
pour garder les résultats d'outils exploitables en interne, puis on les
retire du texte affiché.
"""

import html
import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("pliage.parser")

_TAG_RE = re.compile(r"<details\b[^>]*>|</details\s*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
_SUMMARY_RE = re.compile(r"<summary[^>]*>.*?</summary>", re.DOTALL | re.IGNORECASE)

KNOWLEDGE_TOOL_HINTS = ("knowledge", "retriev", "search_files", "query_doc")


@dataclass
class DetailsBlock:
    type: str
    attrs: dict
    inner: str
    start: int
    end: int


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: object
    raw_result: str = ""

    @property
    def query(self):
        for key in ("query", "q", "search_query", "queries"):
            value = self.arguments.get(key)
            if isinstance(value, list):
                return " | ".join(str(v) for v in value)
            if value:
                return str(value)
        return None


@dataclass
class KnowledgeResult:
    """Un passage documentaire renvoyé par un outil de connaissance."""

    content: str
    source: str | None = None
    file_id: str | None = None
    distance: float | None = None
    page: str | None = None
    query: str | None = None
    tool: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class DocumentSource:
    name: str
    file_id: str | None = None


# ------------------------------------------------------------
# JSON
# ------------------------------------------------------------

def decode_nested_json(value, max_depth=6):
    """Décode un JSON encodé plusieurs fois (jamais d'eval).

    S'arrête dès que la valeur n'est plus une chaîne JSON valide.
    """
    current = value
    for _ in range(max_depth):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text:
            return current
        try:
            current = json.loads(text)
            continue
        except ValueError:
            pass
        unescaped = html.unescape(text)
        if unescaped == text:
            return current
        try:
            current = json.loads(unescaped)
        except ValueError:
            return current
    return current


# ------------------------------------------------------------
# Blocs <details>
# ------------------------------------------------------------

def _parse_attrs(tag):
    attrs = {}
    for name, dq, sq in _ATTR_RE.findall(tag):
        attrs[name.lower()] = html.unescape(dq if dq or not sq else sq)
    return attrs


def _make_block(open_match, inner, end):
    attrs = _parse_attrs(open_match.group(0))
    return DetailsBlock(
        type=attrs.get("type", "").lower(),
        attrs=attrs,
        inner=inner,
        start=open_match.start(),
        end=end,
    )


def parse_openwebui_details(content):
    """Retourne les blocs <details> de premier niveau, imbrication gérée."""
    if not content or "<details" not in content.lower():
        return []

    blocks = []
    depth = 0
    open_match = None

    for match in _TAG_RE.finditer(content):
        if not match.group(0).startswith("</"):
            if depth == 0:
                open_match = match
            depth += 1
            continue
        if depth == 0:
            continue  # fermeture orpheline
        depth -= 1
        if depth == 0:
            inner = content[open_match.end():match.start()]
            blocks.append(_make_block(open_match, inner, match.end()))
            open_match = None

    if depth and open_match is not None:
        # Bloc non fermé (réponse tronquée) : il court jusqu'à la fin.
        inner = content[open_match.end():]
        blocks.append(_make_block(open_match, inner, len(content)))
    return blocks


def _result_from_inner(inner):
    text = _SUMMARY_RE.sub("", inner).strip()
    return re.sub(r"^```\w*\s*|\s*```$", "", text)


def extract_tool_calls(content):
    calls = []
    for block in parse_openwebui_details(content):
        if block.type not in ("tool_calls", "tool_call"):
            continue
        arguments = decode_nested_json(block.attrs.get("arguments", ""))
        if not isinstance(arguments, dict):
            arguments = {"raw": arguments} if arguments else {}
        raw = block.attrs.get("result")
        if raw is None:
            raw = _result_from_inner(block.inner)
        calls.append(ToolCall(
            name=block.attrs.get("name", ""),
            arguments=arguments,
            result=decode_nested_json(raw),
            raw_result=raw or "",
        ))
    return calls


# ------------------------------------------------------------
# Résultats de connaissance
# ------------------------------------------------------------

def _first(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _item_to_result(item, query=None, tool=None):
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    content = _first(item, "content", "text", "document", "page_content", "chunk")
    if not isinstance(content, str) or not content.strip():
        return None

    source = _first(item, "source", "name", "filename", "file_name", "title")
    if isinstance(source, dict):
        source = _first(source, "name", "filename", "title", "id")
    if not source:
        source = _first(meta, "name", "source", "filename", "file_name", "title")

    file_id = _first(item, "file_id", "fileId") or _first(meta, "file_id", "fileId")
    if not file_id and isinstance(item.get("file"), dict):
        file_id = item["file"].get("id")

    page = _first(item, "page_label", "page")
    if page is None:
        page = _first(meta, "page_label", "page")
    distance = _first(item, "distance", "score")
    if distance is None:
        distance = _first(meta, "distance", "score")

    return KnowledgeResult(
        content=html.unescape(content).strip(),
        source=str(source).strip() if source else None,
        file_id=str(file_id) if file_id else None,
        distance=_to_float(distance),
        page=str(page) if page is not None else None,
        query=query,
        tool=tool,
    )


def _iter_column_format(obj):
    documents = obj.get("documents") or obj.get("document")
    if documents and isinstance(documents[0], list):  # [[...]]
        documents = documents[0]
    metadatas = obj.get("metadatas") or obj.get("metadata") or []
    distances = obj.get("distances") or []
    if metadatas and isinstance(metadatas[0], list):
        metadatas = metadatas[0]
    if distances and isinstance(distances[0], list):
        distances = distances[0]
    parent_source = obj.get("source")

    for index, text in enumerate(documents):
        meta = metadatas[index] if index < len(metadatas) else {}
        meta = meta if isinstance(meta, dict) else {}
        item = {"content": text, "metadata": meta}
        if index < len(distances):
            item["distance"] = distances[index]
        if not _first(meta, "name", "source", "filename") and parent_source:
            item["source"] = parent_source
        yield item


def _iter_items(obj, depth=0):
    """Parcourt les formes de résultats connues (liste, dict, colonnes)."""
    if depth > 6:
        return
    if isinstance(obj, str):
        obj = decode_nested_json(obj)

    if isinstance(obj, list):
        for element in obj:
            yield from _iter_items(element, depth + 1)
        return
    if not isinstance(obj, dict):
        return

    if (
        _first(obj, "content", "text", "page_content", "chunk") is not None
        or isinstance(obj.get("document"), str)
    ):
        yield obj
        return

    documents = obj.get("documents") or obj.get("document")
    if isinstance(documents, list) and documents and not isinstance(documents[0], dict):
        yield from _iter_column_format(obj)
        return

    for key in ("results", "items", "chunks", "data", "documents", "files"):
        if isinstance(obj.get(key), list):
            yield from _iter_items(obj[key], depth + 1)
            return


def is_knowledge_tool(name):
    lowered = (name or "").lower()
    return any(hint in lowered for hint in KNOWLEDGE_TOOL_HINTS)


def extract_knowledge_results(message_or_content, include_message_sources=True):
    """Extrait tous les passages documentaires d'un message assistant.

    Sources examinées : blocs tool_calls du `content`, puis
    `message["sources"]` (citations OpenWebUI) le cas échéant.
    """
    if isinstance(message_or_content, dict):
        message = message_or_content
        content = message.get("content") or ""
    else:
        message = {}
        content = message_or_content or ""

    results = []
    for call in extract_tool_calls(content):
        if not is_knowledge_tool(call.name):
            continue
        before = len(results)
        for item in _iter_items(call.result):
            result = _item_to_result(item, query=call.query, tool=call.name)
            if result:
                results.append(result)
        log.debug("tool %s query=%r -> %d passages", call.name, call.query, len(results) - before)

    if include_message_sources and isinstance(message.get("sources"), list):
        for item in _iter_items(message["sources"]):
            result = _item_to_result(item, tool="message.sources")
            if result:
                results.append(result)

    return dedupe_results(results)


def _norm_content(text):
    return re.sub(r"\s+", " ", text).strip().lower()


QUERY_SEPARATOR = " || "


def dedupe_results(results):
    """Fusionne les passages identiques (même fichier, même texte).

    Les requêtes ayant retrouvé le passage sont conservées (séparées par ` || `).
    """
    seen = {}
    unique = []
    for result in results:
        owner = (result.source or result.file_id or "").lower()
        key = (owner, _norm_content(result.content))
        existing = seen.get(key)
        if existing is None:
            seen[key] = result
            unique.append(result)
            continue
        queries = [q for q in (existing.query or "").split(QUERY_SEPARATOR) if q]
        if result.query and result.query not in queries:
            existing.query = QUERY_SEPARATOR.join(queries + [result.query])
        if existing.distance is None:
            existing.distance = result.distance
        existing.page = existing.page or result.page
        existing.file_id = existing.file_id or result.file_id
        existing.source = existing.source or result.source
    return unique


def extract_sources(results):
    sources = []
    seen = set()
    for result in results:
        if not result.source:
            continue
        key = result.source.lower()
        if key in seen:
            continue
        seen.add(key)
        sources.append(DocumentSource(name=result.source, file_id=result.file_id))
    return sources


_LEGACY_SOURCE_PATTERNS = (
    r'"source"\s*:\s*"([^"]+)"',
    r'\\"source\\"\s*:\s*\\"([^"]+?)\\"',
)


def extract_source_names_fallback(content):
    """Ancienne extraction par regex, gardée en dernier recours."""
    if not content:
        return []
    decoded = html.unescape(content)
    names = []
    for pattern in _LEGACY_SOURCE_PATTERNS:
        for name in re.findall(pattern, decoded, flags=re.IGNORECASE):
            name = name.replace("\\n", "").replace('\\"', '"').strip()
            if name and name not in names:
                names.append(name)
    return names


# ------------------------------------------------------------
# Texte affiché
# ------------------------------------------------------------

def clean_final_answer(content):
    """Texte destiné à l'utilisateur : sans blocs techniques OpenWebUI."""
    if not content:
        return ""
    parts = []
    cursor = 0
    for block in parse_openwebui_details(content):
        parts.append(content[cursor:block.start])
        cursor = block.end
    parts.append(content[cursor:])
    text = html.unescape("".join(parts))
    text = _SUMMARY_RE.sub("", text)
    text = re.sub(r"</?details[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
