"""Optional page-image review through the configured Pliage model."""
import base64
import json
import re

import pymupdf

from pliage.pdf_preparation import open_pdf
from rag.extractor import to_candidates
from rag.text import normalize
from rag.retrieval import source_matches_target
from rag.schemas import Evidence

MAX_REVIEW_PAGES = 5


def parse_pages(text, page_count):
    pages = set()
    for part in text.split(','):
        match = re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*', part)
        if not match:
            raise ValueError('Pages : utilise par exemple 3,7 ou 3-5.')
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start or end > page_count:
            raise ValueError('Numéro de page hors du PDF.')
        if end - start + 1 > MAX_REVIEW_PAGES:
            raise ValueError('Maximum 5 pages par relecture.')
        pages.update(range(start, end + 1))
    if not pages or len(pages) > MAX_REVIEW_PAGES:
        raise ValueError('Sélectionne entre 1 et 5 pages.')
    return sorted(pages)



def _staff_section(text, indicator, quote):
    """Scope a visual staff quote to an explicit sequential group heading.

    Flat side-by-side tables without sequential headings remain ambiguous.
    The quote must occur verbatim (apart from Markdown and spacing) in exactly
    one section of the requested group, never just somewhere on the page.
    """
    group = "associes" if indicator.startswith("associes_") else "vacataires"
    lines = text.splitlines(keepends=True)
    sections = []
    current, content = None, []
    for line in lines:
        heading = re.fullmatch(
            r"(?:\d+\s+)?professionnels?\s+(associes?(?:\s+signataires?)?|vacataires?)",
            normalize(line).strip(" #*:-|"),
        )
        if heading:
            if current:
                sections.append((current, ''.join(content)))
            current = "associes" if heading[1].startswith("associ") else "vacataires"
            content = [line]
        elif current:
            content.append(line)
    if current:
        sections.append((current, ''.join(content)))
    clean = lambda value: normalize(re.sub(r"[|*_#>`]+", " ", value))
    query = clean(quote)
    matches = [section for label, section in sections
               if label == group and query and query in clean(section)]
    return matches[0] if len(matches) == 1 else None


def visual_candidates(raws, item):
    """Use section context only for visual staff; keep all rejected traces."""
    candidates = []
    for raw in raws:
        indicator = str(raw.get('indicator', ''))
        is_staff = indicator.startswith(('associes_', 'vacataires_')) and not indicator.endswith('_total')
        if is_staff:
            section = _staff_section(item.content, indicator, str(raw.get('quote') or ''))
            if section is not None:
                scoped = Evidence(chunk_id=item.chunk_id, content=section, source=item.source,
                                  page=item.page, families=item.families)
                candidates.extend(to_candidates([raw], [scoped]))
                continue
        candidates.extend(to_candidates([raw], [item]))
    return candidates

def review_pages(client, plan, indicators, options, on_step):
    if not plan.target_file or not source_matches_target(options['name'], plan.target_file):
        raise ValueError('Le PDF joint doit correspondre au document ciblé dans la question.')
    if not options['model'].strip():
        raise ValueError('Renseigne un modèle Pliage compatible images.')
    evidence, candidates = [], []
    with open_pdf(options['data']) as document:
        pages = parse_pages(options['pages'], len(document))
        for page_number in pages:
            on_step(f'Relecture visuelle de la page {page_number}…')
            page = document[page_number - 1]
            scale = min(2, 2200 / max(page.rect.width, page.rect.height))
            png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes('png')
            chunk_id = f'V{page_number}'
            prompt = (
                'Lis uniquement cette image. Transcris les tableaux ligne par ligne en conservant '
                'les en-têtes et les colonnes associés/vacataires, résultat/points/euros. '
                'Effectifs : écris une section professionnels associés signataires puis une section '
                'professionnels vacataires, chacune suivie de ses effectifs par profession. '
                'Protocoles : zoome mentalement sur la cellule Détail de l’indicateur et transcris '
                'son nombre visible, distinct des points, du plafond et du montant. Ne le calcule pas. '
                'Ne complète aucun chiffre illisible. Une condition IPA ne prouve pas une présence. '
                'Renvoie un objet JSON avec transcription (texte) et candidates (liste). '
                'Chaque candidat contient indicator, value, value_type="establishment_value", '
                f'chunk="{chunk_id}", quote (citation exacte dans ta transcription), period. '
                'N’inclus que les valeurs visibles et lisibles. Indicateurs autorisés : '
                + ', '.join(f'{ind.key}: {ind.label}' for ind in indicators)
            )
            response = client.call_standard([{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}},
            ]}], options['model'].strip(), timeout=180)
            clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', response.strip())
            data = json.loads(clean)
            transcription = data.get('transcription')
            raws = data.get('candidates')
            if not isinstance(transcription, str) or not transcription.strip() or not isinstance(raws, list):
                raise ValueError('Relecture visuelle sans transcription ou candidats valides.')
            item = Evidence(chunk_id=chunk_id, content=transcription, source=options['name'],
                            page=str(page_number), families=list(plan.families))
            allowed = {ind.key for ind in indicators}
            raws = [dict(raw, chunk=chunk_id) for raw in raws
                    if isinstance(raw, dict) and raw.get('indicator') in allowed]
            evidence.append(item)
            candidates.extend(visual_candidates(raws, item))
    return evidence, candidates


def apply_visual_review(report, evidence, candidates, indicators):
    from rag.consolidator import consolidate, same_value
    from rag.schemas import CONFIRMED, NEEDS_VERIFICATION

    previous = report.results
    report.evidence.extend(evidence)
    report.candidates.extend(candidates)
    report.results = consolidate(report.candidates, indicators)
    for candidate in candidates:
        if not candidate.usable:
            continue
        result = report.results[candidate.indicator]
        old = previous.get(candidate.indicator)
        kind = next(ind.kind for ind in indicators if ind.key == candidate.indicator)
        agreement = (old and old.status == CONFIRMED and old.value is not None
                     and same_value(old.value, candidate.value, kind))
        if result.status == CONFIRMED and not agreement:
            result.flag('Lecture visuelle différente ou sans confirmation OCR fiable : vérifie la page image.')
    report.warnings.append('Relecture visuelle : transcription par modèle, à comparer au PDF. Aucun OCR distant remplacé.')
