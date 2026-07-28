"""Country-specific search query expansion.

The profile remains the source of truth for target roles.  Country packs add
local-language retrieval aliases at scan time so users do not have to pollute
their profile with translations of the same role.
"""

from __future__ import annotations


BRAZIL_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "director supply chain": (
        "Diretor de Supply Chain",
        "Diretor de Cadeia de Suprimentos",
    ),
    "head of procurement": (
        "Diretor de Suprimentos",
        "Head de Compras",
    ),
    "head of supply chain": (
        "Head de Supply Chain",
        "Head de Cadeia de Suprimentos",
    ),
    "director operations": (
        "Diretor de Operações",
        "Diretor de Operações Industriais",
    ),
    "chief procurement officer": (
        "Diretor Executivo de Suprimentos",
        "Diretor Executivo de Compras",
    ),
    "director purchasing": (
        "Diretor de Compras",
        "Diretor de Procurement",
    ),
    "head of sourcing": (
        "Head de Sourcing",
        "Head de Compras Estratégicas",
    ),
    "director logistics": (
        "Diretor de Logística",
        "Diretor de Operações Logísticas",
    ),
    "head of operations": (
        "Head de Operações",
        "Gerente Executivo de Operações",
    ),
    "global supply chain director": (
        "Diretor Global de Supply Chain",
        "Diretor Global de Cadeia de Suprimentos",
    ),
    "interim manager": ("Gerente Interino",),
    "crisis manager": ("Gerente de Crise",),
    "krisenmanager": ("Gerente de Crise",),
    "crisis director": ("Diretor de Gestão de Crise",),
    "turnaround": (
        "Diretor de Turnaround",
        "Diretor de Reestruturação",
    ),
    "supply chain transformation": (
        "Diretor de Transformação de Supply Chain",
        "Transformação da Cadeia de Suprimentos",
    ),
    "e2e supply chain": ("Supply Chain Ponta a Ponta",),
    "chief restructuring officer": (
        "Chief Restructuring Officer",
        "Diretor de Reestruturação",
    ),
    "cro": (
        "Chief Restructuring Officer",
        "Diretor de Reestruturação",
    ),
    "growth director": ("Diretor de Growth",),
    "growth manager": ("Gerente de Growth",),
    "head of autonomous operations": ("Head de Operações Autônomas",),
    "ai agent orchestrator": ("Orquestrador de Agentes de IA",),
    "supply chain transformation & ai": ("Transformação de Supply Chain e IA",),
    "director of ai strategy": ("Diretor de Estratégia de IA",),
    "chief of staff ai": ("Chief of Staff de IA",),
}


def _normalise_query(value: str) -> str:
    return " ".join(value.casefold().split())


def expand_queries_for_country(queries: list[str], country: str) -> list[str]:
    """Return ordered, de-duplicated search queries for a country.

    Original profile titles stay first within each priority group.  Their
    local aliases follow immediately, which keeps source window rotation fair
    and ensures both English and Portuguese executive postings are covered.
    """
    if country.casefold() != "br":
        return list(queries)

    expanded: list[str] = []
    seen: set[str] = set()
    for query in queries:
        candidates = (query, *BRAZIL_QUERY_ALIASES.get(_normalise_query(query), ()))
        for candidate in candidates:
            normalised = _normalise_query(candidate)
            if normalised and normalised not in seen:
                expanded.append(candidate)
                seen.add(normalised)
    return expanded
