import pytest

from app.models.job import Job
from app.models.user import UserProfile
from app.scoring.rules import (
    detect_description_language,
    is_commercial_function_title,
    matches_explicit_target_title,
    pre_filter,
)
from app.services.scheduler_service import _is_hidden_country
from app.sources.adzuna import ADZUNA_CURRENCIES, ADZUNA_SUPPORTED
from app.sources.aggregator import _is_wrong_location
from app.sources.base import RawJob, SearchParams
from app.sources.country_queries import expand_queries_for_country
from app.sources.gupy import GupyFeedSource
from app.sources.jobspy_source import COUNTRY_NAME
from app.sources.jooble import COUNTRY_LOCATIONS


def test_brazil_is_supported_by_available_aggregators():
    assert "br" in ADZUNA_SUPPORTED
    assert ADZUNA_CURRENCIES["br"] == "BRL"
    assert COUNTRY_NAME["br"] == "brazil"
    assert COUNTRY_LOCATIONS["br"] == ("Brazil", "BR")


def test_brazil_queries_interleave_english_and_portuguese_aliases():
    queries = expand_queries_for_country(
        ["Director Operations", "Head of Procurement"],
        "br",
    )

    assert queries[:3] == [
        "Director Operations",
        "Diretor de Operações",
        "Diretor de Operações Industriais",
    ]
    assert "Diretor de Suprimentos" in queries
    assert expand_queries_for_country(["Director Operations"], "de") == ["Director Operations"]


def test_search_params_use_country_specific_queries_only_for_brazil():
    params = SearchParams(
        queries=["Director Operations"],
        countries=["de", "br"],
        country_queries={"br": ["Diretor de Operações"]},
    )

    assert params.queries_for_country("DE") == ["Director Operations"]
    assert params.queries_for_country("BR") == ["Diretor de Operações"]


def test_sao_paulo_is_allowed_only_when_brazil_is_opted_in():
    job = RawJob(
        external_id="br-1",
        source="test",
        title="Diretor de Supply Chain",
        location="São Paulo, SP",
    )

    assert _is_wrong_location(job) is True
    assert _is_wrong_location(job, frozenset({"br"})) is False


def test_hidden_brazil_is_suppressed_from_default_delivery():
    profile = UserProfile(hidden_countries=["br"])

    assert _is_hidden_country(Job(country="BR"), profile) is True
    assert _is_hidden_country(Job(country="de"), profile) is False


def test_portuguese_description_is_detected_but_not_rejected_by_itself():
    description = (
        "Nossa empresa procura uma liderança para a equipe de operações. "
        "Você será responsável pela cadeia de suprimentos e pela gestão de "
        "fornecedores. A pessoa terá experiência em planejamento, distribuição "
        "e melhoria contínua. As responsabilidades incluem desenvolver a equipe, "
        "garantir os requisitos da empresa e trabalhar com compras e logística."
    )
    job = Job(title="Diretor de Supply Chain", description=description)

    assert detect_description_language(description) == "pt"
    assert pre_filter(job, UserProfile(english_only=False)) == (True, "high")


def test_explicit_portuguese_requirement_is_rejected():
    job = Job(
        title="Diretor de Supply Chain",
        description=(
            "Multinational supply chain role. Português fluente obrigatório para negociação com fornecedores locais."
        ),
    )

    assert pre_filter(job, UserProfile(english_only=False)) == (False, "low")


def test_portuguese_alias_is_protected_as_an_explicit_target():
    profile = UserProfile(
        target_titles=["Director Operations"],
        english_only=False,
    )
    job = Job(
        title="Diretor de Operações",
        description="Liderar a transformação e a equipe industrial.",
    )

    assert matches_explicit_target_title(job.title, profile) is True
    assert pre_filter(job, profile) == (True, "high")


@pytest.mark.parametrize(
    "title",
    [
        "Diretor de Operações Comerciais",
        "Diretora de Operações de Varejo",
        "Diretor Comercial",
        "Diretora de Vendas",
    ],
)
def test_portuguese_commercial_titles_are_rejected(title):
    job = Job(
        title=title,
        description="Gestão de distribuição, fornecedores e operações.",
    )

    assert is_commercial_function_title(title) is True
    assert pre_filter(job, UserProfile()) == (False, "low")


def test_plain_portuguese_manager_stays_in_tier2():
    job = Job(
        title="Gerente de Compras",
        description="Gestão de compras estratégicas e fornecedores.",
    )

    assert pre_filter(job, UserProfile()) == (False, "manager_tier2")


@pytest.mark.parametrize(
    "title",
    [
        "Estágio de Custos",
        "Auxiliar de Escritório",
        "Operador de Loja",
        "Recepcionista",
        "Supervisor de Logística",
        "Executivo de Negócios Jr.",
        "Gerente Comercial",
        "Key Account Manager",
    ],
)
def test_brazilian_junior_and_commercial_noise_is_rejected_early(title):
    job = Job(
        title=title,
        description="Operações, logística, compras e fornecedores.",
    )

    assert pre_filter(job, UserProfile()) == (False, "low")


@pytest.mark.asyncio
async def test_gupy_official_feed_parses_relevant_brazilian_jobs(monkeypatch):
    payload = {
        "companies": [
            {
                "name": "Empresa Brasil",
                "subdomain": "empresa-brasil",
                "url": "https://empresa-brasil.gupy.io",
                "jobs": [
                    {
                        "id": 123,
                        "title": "Diretor de Operações",
                        "publishingDate": "2026-07-27 12:00:00",
                        "url": "https://empresa-brasil.gupy.io/jobs/123",
                        "address": {
                            "fullAddress": "São Paulo, SP",
                            "country": "BR",
                        },
                        "description": "<p>Liderar operações industriais.</p>",
                        "prerequisites": "<p>Inglês avançado.</p>",
                        "responsibilities": "<p>Transformação operacional.</p>",
                        "remoteWorking": False,
                    },
                    {
                        "id": 124,
                        "title": "Analista Financeiro",
                        "address": {"city": "São Paulo", "country": "BR"},
                    },
                ],
            }
        ]
    }

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, **_kwargs):
            return payload

    class FakeSession:
        def __init__(self, **_kwargs):
            self.headers = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, _url, headers):
            self.headers = headers
            return FakeResponse()

    monkeypatch.setattr("app.sources.gupy.aiohttp.ClientSession", FakeSession)
    monkeypatch.setattr("app.sources.gupy.settings.gupy_feed_url", "https://feed.example/jobs")
    monkeypatch.setattr("app.sources.gupy.settings.gupy_feed_token", "secret-token")

    params = SearchParams(
        queries=["Director Operations"],
        countries=["br"],
        country_queries={"br": ["Director Operations", "Diretor de Operações"]},
    )
    jobs = await GupyFeedSource().search(params)

    assert len(jobs) == 1
    assert jobs[0].external_id == "gupy_empresa-brasil_123"
    assert jobs[0].country == "BR"
    assert jobs[0].location == "São Paulo, SP"
    assert jobs[0].source == "gupy"
