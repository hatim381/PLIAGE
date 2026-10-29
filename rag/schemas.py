"""Structures typées du pipeline multi-RAG."""

from dataclasses import dataclass, field

# Types de valeur
ESTABLISHMENT = "establishment_value"
METHODOLOGICAL = "methodological_reference"
MAXIMUM = "maximum"
THRESHOLD = "threshold"
POINTS = "points"
REMUNERATION = "remuneration"
UNKNOWN = "unknown"

VALUE_TYPES = (
    ESTABLISHMENT, METHODOLOGICAL, MAXIMUM, THRESHOLD, POINTS, REMUNERATION, UNKNOWN,
)

# Statuts
CONFIRMED = "confirmed"
NOT_FOUND = "not_found"
CONFLICT = "conflict"
NEEDS_VERIFICATION = "needs_verification"


@dataclass(frozen=True)
class IndicatorDef:
    key: str
    label: str
    family: str
    kind: str  # count | percent | money | points | text
    unit: str = ""
    description: str = ""
    optional: bool = False  # masqué du tableau s'il n'est pas trouvé
    queries: tuple = ()  # recherches ciblées si l'indicateur manque après la 1re passe


@dataclass(frozen=True)
class Family:
    key: str
    label: str
    triggers: tuple
    keywords: tuple  # pertinence d'un passage pour la famille
    queries: tuple  # reformulations successives (une par tentative)


@dataclass
class RoutePlan:
    question: str
    families: list
    target_file: str | None = None
    target_label: str | None = None
    years: list = field(default_factory=list)

    @property
    def is_extraction(self):
        return bool(self.families)


@dataclass
class RAGQuery:
    family: str
    text: str
    attempt: int


@dataclass
class Evidence:
    """Passage documentaire conservé comme preuve."""

    chunk_id: str
    content: str
    source: str | None
    file_id: str | None = None
    distance: float | None = None
    page: str | None = None
    queries: list = field(default_factory=list)
    families: list = field(default_factory=list)  # pertinence (mots-clés + requête)
    searched_by: list = field(default_factory=list)  # familles dont la requête l'a retrouvé


@dataclass
class Candidate:
    indicator: str
    value: object
    unit: str = ""
    value_type: str = UNKNOWN
    llm_value_type: str = UNKNOWN
    chunk_id: str | None = None
    quote: str = ""
    period: str | None = None
    source: str | None = None
    file_id: str | None = None
    query: str | None = None
    grounded: bool = False
    rejection: str | None = None
    note: str | None = None  # raison d'un type `unknown` imposé par Python

    @property
    def usable(self):
        return self.grounded and self.rejection is None and self.value_type == ESTABLISHMENT


@dataclass
class IndicatorResult:
    indicator: str
    label: str
    status: str = NOT_FOUND
    value: object = None
    unit: str = ""
    period: str | None = None
    value_type: str | None = None
    source: str | None = None
    file_id: str | None = None
    evidence: str = ""
    chunk_id: str | None = None
    query: str | None = None
    optional: bool = False
    candidates: list = field(default_factory=list)
    messages: list = field(default_factory=list)

    def flag(self, message):
        """Une valeur confirmée devient « à vérifier » ; la raison est gardée."""
        if self.status == CONFIRMED:
            self.status = NEEDS_VERIFICATION
        self.messages.append(message)


@dataclass
class ValidationCheck:
    name: str
    ok: bool
    message: str
    indicators: tuple = ()


@dataclass
class ExtractionReport:
    plan: RoutePlan
    queries: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    discarded_evidence: int = 0
    candidates: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    debug: dict = field(default_factory=dict)
    markdown: str = ""
