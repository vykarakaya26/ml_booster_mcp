"""Finding a brand or company name among the players of the entity-level sources.

Names are compared after Turkish-aware folding: company form words (A.Ş., T.A.O.) are dropped, sector words (Sigorta,
Bankası) only count as a tie-breaker. An alias of the catalog or an exact name resolves the brand; a partial or fuzzy
match is only offered as a candidate, the system does not guess.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from mlbooster.domain.errors import NotFound
from mlbooster.domain.model import BrandEntry, BrandGroup, BrandLexicon

RESOLVED, AMBIGUOUS, NOT_FOUND = "resolved", "ambiguous", "not_found"
ALIAS, EXACT, PARTIAL, FUZZY, CHOICE, GROUP = "alias", "exact", "partial", "fuzzy", "choice", "group"
GROUP_PREFIX = "GROUP."

MAX_CANDIDATES = 10
MAX_OTHERS = 5
MIN_SCORE = 0.7
FUZZY_MIN_RATIO = 0.82
FUZZY_MIN_LENGTH = 4
PREFIX_MIN_LENGTH = 4
PLACEHOLDER_NAMES = frozenset({"diger", "digerleri", "other", "others"})  # residual rows of a source, not a player

_NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class EntityRecord:
    source: str
    entity_id: str
    name: str
    names: tuple[str, ...] = ()  # every spelling seen in the source, latest first
    series: tuple[str, ...] = ()
    last_date: str | None = None


@dataclass(frozen=True)
class EntityScan:
    records: tuple[EntityRecord, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)  # source id → why it could not be read


@dataclass(frozen=True)
class BrandMatch:
    name: str
    source: str
    sector: str
    entity_ids: tuple[str, ...]
    entity_names: tuple[str, ...]
    score: float
    kind: str
    group: BrandGroup | None = None  # the share of a group is computed as the sum of its members

    @property
    def id(self) -> str:
        return f"{self.source}:{GROUP_PREFIX}{self.group.id}" if self.group else f"{self.source}:{'+'.join(self.entity_ids)}"

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "source": self.source, "sector": self.sector,
                "entity_ids": list(self.entity_ids), "entity_names": list(self.entity_names),
                "score": round(self.score, 2), "kind": self.kind}  # fmt: skip


@dataclass(frozen=True)
class BrandResolution:
    query: str
    status: str
    matches: tuple[BrandMatch, ...] = ()  # resolved: one; ambiguous: the candidates to choose from
    others: tuple[BrandMatch, ...] = ()  # weaker names that also matched, shown so that the choice stays visible
    total: int = 0  # all candidates before the list was cut
    known: BrandEntry | None = None  # catalog entry of a brand without an entity-level source

    @property
    def match(self) -> BrandMatch:
        return self.matches[0]


def fold(text: str) -> str:
    """ASCII lower case without Turkish or other diacritics, whichever way they are encoded: 'İŞ' → 'is'."""
    decomposed = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("I", "i"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def tokens(text: str, lexicon: BrandLexicon) -> tuple[str, ...]:
    """Folded words without company form words; a name made only of such words keeps them."""
    words = tuple(w for w in _NON_WORD.split(fold(text).replace(".", "").replace("'", "")) if w)
    return tuple(w for w in words if w not in lexicon.legal_tokens) or words


def is_placeholder(name: str) -> bool:
    return fold(name).strip() in PLACEHOLDER_NAMES


def _core(words: tuple[str, ...], lexicon: BrandLexicon) -> tuple[str, ...]:
    return tuple(w for w in words if w not in lexicon.generic_tokens) or words


def score_name(query: str, name: str, lexicon: BrandLexicon) -> tuple[float, str] | None:
    """Similarity of the typed text to a name, with its kind; None below the candidate threshold."""
    q, n = tokens(query, lexicon), tokens(name, lexicon)
    if not q or not n:
        return None
    if "".join(q) == "".join(n):
        return 0.99, EXACT
    q_core, n_core = _core(q, lexicon), _core(n, lexicon)
    q_generic = set(q) - set(q_core)
    fits = q_generic <= set(n)  # "Anadolu Sigorta" must not be read as "Anadolu Hayat"
    if "".join(q_core) == "".join(n_core) and fits:
        return 0.96, EXACT
    penalty = 0 if fits else 0.1
    if set(q_core) <= set(n):
        return 0.75 + 0.15 * min(1.0, len(q_core) / len(n_core)) - penalty, PARTIAL
    if all(len(w) >= PREFIX_MIN_LENGTH and any(t.startswith(w) for t in n) for w in q_core):
        return 0.74 - penalty / 2, PARTIAL
    text = "".join(q_core)
    if len(text) < FUZZY_MIN_LENGTH:
        return None
    ratio = max(SequenceMatcher(None, text, candidate).ratio() for candidate in ("".join(n_core), *n_core))
    return (0.85 * ratio, FUZZY) if ratio >= FUZZY_MIN_RATIO else None


def _names_of(record: EntityRecord) -> tuple[str, ...]:
    return tuple(dict.fromkeys((record.name, *record.names)))


def _scored(query: str, record: EntityRecord, lexicon: BrandLexicon) -> tuple[float, str] | None:
    hits = [hit for name in _names_of(record) if (hit := score_name(query, name, lexicon)) is not None]
    return max(hits, default=None)


def _entry_for(query: str, lexicon: BrandLexicon) -> BrandEntry | None:
    key = "".join(tokens(query, lexicon))
    return next(
        (e for e in lexicon.entries if key in {"".join(tokens(a, lexicon)) for a in (e.name, *e.aliases)}),
        None,
    )


def _known_for(query: str, lexicon: BrandLexicon) -> BrandEntry | None:
    """Catalog brand without an entity-level source that the text names, even partly ("Kuveyt" → "Kuveyt Türk")."""
    return next(
        (e for e in lexicon.entries if not e.targets and any((hit := score_name(query, a, lexicon)) and hit[1] == EXACT for a in (e.name, *e.aliases))),
        None,
    )


def _targets(entry: BrandEntry, by_key: dict, sector_of: dict[str, str]) -> list[BrandMatch]:
    """Catalog targets whose players the scan has actually seen."""
    grouped = len(entry.targets) > 1
    out = []
    for target in entry.targets:
        found = [by_key[(target.source, e)] for e in target.entities if (target.source, e) in by_key]
        if found and target.source in sector_of:
            name = found[0].name if grouped else entry.name
            out.append(BrandMatch(name, target.source, sector_of[target.source], tuple(r.entity_id for r in found),
                                  tuple(r.name for r in found), 1.0, ALIAS))  # fmt: skip
    return out


def _group_for(query: str, lexicon: BrandLexicon) -> BrandGroup | None:
    key = "".join(tokens(query, lexicon))
    return next((g for g in lexicon.groups if key in {"".join(tokens(a, lexicon)) for a in (g.name, *g.aliases)}), None)


def _group_match(group: BrandGroup, by_key: dict, sector_of: dict[str, str]) -> BrandMatch | None:
    """A group needs at least one member the scan has seen; members the source does not list stay out."""
    found = [by_key[(group.source, m.entity)] for m in group.members if (group.source, m.entity) in by_key]
    if not found or group.source not in sector_of:
        return None
    return BrandMatch(group.name, group.source, sector_of[group.source], tuple(r.entity_id for r in found), tuple(r.name for r in found), 1.0, GROUP, group)


def resolve(query: str, scan: EntityScan, lexicon: BrandLexicon, sector_of: dict[str, str]) -> BrandResolution:
    """`sector_of`: source id → sector whose market share list holds it."""
    by_key = {(r.source, r.entity_id): r for r in scan.records}
    if (group := _group_for(query, lexicon)) and (match := _group_match(group, by_key, sector_of)):
        return BrandResolution(query, RESOLVED, (match,), (), 1)
    entry = _entry_for(query, lexicon)
    aliased = _targets(entry, by_key, sector_of) if entry else []
    taken = {(m.source, e) for m in aliased for e in m.entity_ids}
    named = []
    for record in scan.records:
        hit = _scored(query, record, lexicon)
        if hit is None or hit[0] < MIN_SCORE or (record.source, record.entity_id) in taken or record.source not in sector_of:
            continue
        named.append(BrandMatch(record.name, record.source, sector_of[record.source], (record.entity_id,), (record.name,), *hit))
    named.sort(key=lambda m: (-m.score, m.name))
    related = tuple([m for m in named if m.kind != FUZZY][:MAX_OTHERS])
    if len(aliased) == 1:
        return BrandResolution(query, RESOLVED, tuple(aliased), related, 1)
    if aliased:
        return BrandResolution(query, AMBIGUOUS, tuple(aliased), related, len(aliased))
    top = named[0] if named else None
    if top is None or top.kind != EXACT:
        if known := _known_for(query, lexicon):
            return BrandResolution(query, NOT_FOUND, (), related, 0, known)
    if top is None:
        return BrandResolution(query, NOT_FOUND)
    if top.kind == EXACT and (len(named) == 1 or named[1].score < top.score):
        return BrandResolution(query, RESOLVED, (top,), tuple(m for m in related if m is not top), 1)
    return BrandResolution(query, AMBIGUOUS, tuple(named[:MAX_CANDIDATES]), (), len(named))


def pick(query: str, choice: str, scan: EntityScan, lexicon: BrandLexicon, sector_of: dict[str, str]) -> BrandResolution:
    """The candidate the user chose (`BrandMatch.id`); it must be a player the scan has seen."""
    source, _, ids = choice.partition(":")
    by_key = {(r.source, r.entity_id): r for r in scan.records}
    if ids.startswith(GROUP_PREFIX):
        group = next((g for g in lexicon.groups if g.source == source and g.id == ids.removeprefix(GROUP_PREFIX)), None)
        match = _group_match(group, by_key, sector_of) if group else None
        if match is None:
            raise NotFound(f"Seçilen aday kaynakta bulunamadı: {choice}")
        return BrandResolution(query, RESOLVED, (match,), (), 1)
    found = [by_key.get((source, e)) for e in ids.split("+")]
    if not all(found) or source not in sector_of:
        raise NotFound(f"Seçilen aday kaynakta bulunamadı: {choice}")
    entity_ids = tuple(r.entity_id for r in found)
    entry = next((e for e in lexicon.entries for t in e.targets if len(e.targets) == 1 and t.source == source and set(entity_ids) == set(t.entities)), None)
    name = entry.name if entry else found[0].name
    match = BrandMatch(name, source, sector_of[source], entity_ids, tuple(r.name for r in found), 1.0, CHOICE)
    return BrandResolution(query, RESOLVED, (match,), (), 1)


def sector_hints(query: str, known: BrandEntry | None, lexicon: BrandLexicon) -> list[tuple[str, str]]:
    """(sector id, reason) pairs for a name that no entity-level source lists."""
    if known is not None and known.sector:
        return [(known.sector, f"Katalog {known.name} markasını bu sektörde tanımlıyor")]
    words = set(tokens(query, lexicon))
    return [(sector, f"Adda geçen sözcük: {', '.join(sorted(words & set(keys)))}") for sector, keys in lexicon.sector_keywords if words & set(keys)]


def trends_code(query: str, lexicon: BrandLexicon) -> str | None:
    """Ad hoc Google Trends series code of the name (the provider reads `BQT.ILGI.<KEY>`, '_' for a space)."""
    key = "_".join(tokens(query, lexicon)).upper()[:48]
    return f"BQT.ILGI.{key}" if key else None
