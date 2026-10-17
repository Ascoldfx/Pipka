import pytest

from app.markets import korea
from app.models.job import Job
from app.models.user import UserProfile
from app.scoring.rules import pre_filter
from app.sources import greenhouse
from app.sources.base import SearchParams

PROFILE = UserProfile(
    target_titles=["Head of Procurement", "Director Supply Chain"],
    preferred_countries=["de", "kr"],
)
ENGLISH_DESC = (
    "Coupang is looking for a Director to lead inventory allocation across the network. "
    "You will partner with global suppliers and logistics providers. "
    "MBA, master's degree, Korean language ability, or SQL is a plus."
)


@pytest.mark.parametrize("name, expected", [
    ("Coupang", "Coupang"),
    ("쿠팡(주)", "Coupang"),
    ("(주)오리온", "Orion"),
    ("한국바스프(주)", "BASF"),
    ("DHL Supply Chain Korea Ltd.", "DHL Group"),
    ("Kuehne + Nagel Ltd. Korea", "Kuehne+Nagel"),
    ("L'Oréal Korea", "L'Oréal"),
    ("BAT Rothmans Korea", "British American Tobacco"),
    ("Sika Corporation", "Sika"),
    ("Lotte Wellfood Co., Ltd.", "Lotte Wellfood"),
    ("Bath & Body Works", None),
    ("Carhartt", None),
    (None, None),
])
def test_employer_whitelist_matches_whole_tokens(name, expected):
    employer = korea.match_employer(name)
    assert (employer.name if employer else None) == expected


@pytest.mark.parametrize("text", [
    "Basic Qualifications: Bilingual fluency in Korean and English.",
    "Native-level Korean is required.",
    "TOPIK level 5 or above",
    "한국어 능통자",
])
def test_korean_requirement_detected(text):
    assert korea.requires_korean(text)


@pytest.mark.parametrize("text", [
    "MBA, master's degree, Korean language ability, or SQL is a plus.",
    "Strong understanding of the Korean market and excellent communication skills.",
    "You will speak with Korean partners daily.",
    "한국어 가능자 우대",
])
def test_korean_as_plus_or_market_context_is_not_a_requirement(text):
    assert not korea.requires_korean(text)


@pytest.mark.parametrize("text, restricted", [
    ("F-2, F-4, F-5, F-6 visa holders only", True),
    ("Must currently reside in Korea.", True),
    ("We cannot provide visa sponsorship.", True),
    ("Experience with F5 load balancers required", False),
    ("Visa sponsorship and relocation support available.", False),
])
def test_residence_status_restrictions(text, restricted):
    assert korea.restricts_status(text) is restricted


def test_rejection_reasons_in_order():
    title = "Director, Inventory Allocation and Control"
    assert korea.rejection_reason(title, "Carhartt", ENGLISH_DESC) == "employer_not_whitelisted"
    korean_desc = "쿠팡 로지스틱스 서비스에서 SCM 프로세스 혁신을 이끌 리더를 찾습니다. 물류 관련 전공 학사"
    assert korea.rejection_reason(title, "Coupang", korean_desc) == "korean_language_posting"
    assert korea.rejection_reason(
        title, "Coupang", "Lead global SCM. Bilingual fluency in Korean and English."
    ) == "korean_required"
    assert korea.rejection_reason(title, "Coupang", ENGLISH_DESC) is None


def test_pre_filter_applies_whitelist_policy_to_korea_only():
    def job(company, desc, country):
        return Job(title="Director Supply Chain", company_name=company, description=desc, country=country)

    assert pre_filter(job("Coupang", ENGLISH_DESC, "KR"), PROFILE) == (True, "high")
    assert pre_filter(job("Carhartt", ENGLISH_DESC, "KR"), PROFILE) == (False, "low")
    bilingual = ENGLISH_DESC + " Bilingual fluency in Korean and English."
    assert pre_filter(job("Coupang", bilingual, "KR"), PROFILE) == (False, "low")
    # Germany keeps the open-market rules: any employer may pass.
    assert pre_filter(job("Carhartt", ENGLISH_DESC, "DE"), PROFILE) == (True, "high")


class _Response:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self.payload


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url):
        self.calls.append(url)
        return _Response(self.routes[url])


@pytest.mark.asyncio
async def test_greenhouse_fetches_details_only_for_in_market_function_titles():
    base = f"{greenhouse.API_BASE}/coupang/jobs"
    routes = {
        base: {"jobs": [
            {"id": 1, "title": "Director, Inventory Allocation and Control", "location": {"name": "Seoul, South Korea"}},
            {"id": 2, "title": "Director, Supply Chain", "location": {"name": "Taipei, Taiwan"}},
            {"id": 3, "title": "Staff Back-end Engineer", "location": {"name": "Seoul, South Korea"}},
        ]},
        f"{base}/1": {
            "id": 1,
            "title": "Director, Inventory Allocation and Control",
            "location": {"name": "Seoul, South Korea"},
            "content": "&lt;p&gt;Lead inventory&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Korean is a plus&lt;/li&gt;&lt;/ul&gt;",
            "first_published": "2026-10-14T01:00:00-04:00",
            "absolute_url": "https://www.coupang.jobs/en/jobs/?gh_jid=1",
            "departments": [{"name": "Global SCM"}],
        },
    }
    session = _Session(routes)
    employer = korea.match_employer("Coupang")

    jobs = await greenhouse.GreenhouseSource()._board(
        session, "kr", employer, korea.LOCATION_MARKERS, ("inventory", "supply chain")
    )

    assert session.calls == [base, f"{base}/1"]
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.company_name, job.country, job.source) == ("Coupang", "KR", "greenhouse")
    assert job.description == "Lead inventory\nKorean is a plus"
    assert job.posted_at.isoformat() == "2026-10-14T05:00:00"


@pytest.mark.asyncio
async def test_greenhouse_is_idle_unless_a_whitelist_market_is_requested():
    assert await greenhouse.GreenhouseSource().search(SearchParams(queries=[], countries=["de"])) == []
