"""South Korea: whitelist market policy.

Open Korean job boards are almost entirely Korean-language and below the
target seniority, so a Korean vacancy is accepted only when:

1. the employer is on the curated whitelist below (large international
   players that can pay a director 100k USD+ and run international
   procurement or supplier search involving Europe or the CIS);
2. the posting is not written in Korean and does not require Korean;
3. it does not restrict residence status (F-2/F-4/F-5/F-6 only, must
   already live in Korea, no visa sponsorship).

Audit 17.10.2026 (Coupang Greenhouse feed): 287 of 369 Korean postings were
written mostly in Korean, and even "Director, Supply Chain Management (Global
SCM)" required bilingual Korean, so the whitelist alone is not enough.

Only verifiable facts are stored. ``ats`` is set only for a confirmed public
feed; other employers are matched on Indeed/Jooble results by name.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

MARKET_CODE = "kr"

LOCATION_MARKERS = (
    "south korea", "republic of korea", "korea", "seoul", "busan", "incheon",
    "pangyo", "seongnam", "suwon", "daejeon", "daegu", "ulsan", "icheon",
    "cheonan", "pyeongtaek", "대한민국", "서울", "부산", "인천", "경기",
)


@dataclass(frozen=True)
class Employer:
    name: str
    aliases: tuple[str, ...]
    group: str
    origin: str
    # Why the employer matters for international procurement (Europe / CIS).
    link: str
    # ("greenhouse", board token) when a public feed is confirmed.
    ats: tuple[str, str] | None = None


EMPLOYERS: tuple[Employer, ...] = (
    # --- Global e-commerce with English-speaking SCM in Seoul -------------
    Employer(
        "Coupang", ("coupang", "쿠팡", "coupang fulfillment services", "coupang logistics services",
                    "쿠팡풀필먼트서비스", "쿠팡로지스틱스서비스", "coupang eats", "쿠팡이츠"),
        "ecommerce", "US", "Global SCM, direct import sourcing", ("greenhouse", "coupang"),
    ),
    Employer(
        "Woowa Brothers (Delivery Hero)", ("woowa brothers", "우아한형제들", "baemin", "배달의민족",
                                           "delivery hero"),
        "ecommerce", "DE", "Delivery Hero (Berlin) group, B-Mart FMCG procurement",
    ),
    Employer("IKEA Korea", ("ikea", "이케아", "ingka"), "ecommerce", "SE", "European supplier base"),

    # --- European FMCG, beverages, beauty, tobacco ------------------------
    Employer("Nestlé", ("nestle", "네슬레"), "eu_fmcg", "CH", "European HQ procurement"),
    Employer("Unilever", ("unilever", "유니레버"), "eu_fmcg", "GB", "European HQ procurement"),
    Employer("L'Oréal", ("l'oreal", "loreal", "로레알", "엘오케이"), "eu_fmcg", "FR", "European HQ procurement"),
    Employer("Beiersdorf", ("beiersdorf", "바이어스도르프", "nivea"), "eu_fmcg", "DE", "German HQ procurement"),
    Employer("Henkel", ("henkel", "헨켈"), "eu_fmcg", "DE", "German HQ procurement"),
    Employer("Reckitt", ("reckitt", "레킷"), "eu_fmcg", "GB", "European HQ procurement"),
    Employer("Danone", ("danone", "다논"), "eu_fmcg", "FR", "European HQ procurement"),
    Employer("Procter & Gamble", ("procter gamble", "p&g", "피앤지"), "eu_fmcg", "US",
             "Global sourcing incl. Europe"),
    Employer("Mondelez", ("mondelez", "몬델리즈"), "eu_fmcg", "US", "Global sourcing incl. Europe and CIS"),
    Employer("Mars", ("mars wrigley", "mars incorporated", "마스"), "eu_fmcg", "US",
             "Global sourcing incl. Europe and CIS"),
    Employer("Campari Group", ("campari", "캄파리"), "eu_fmcg", "IT", "European HQ supply chain"),
    Employer("Pernod Ricard", ("pernod ricard", "페르노리카"), "eu_fmcg", "FR", "European HQ supply chain"),
    Employer("Diageo", ("diageo", "디아지오"), "eu_fmcg", "GB", "European HQ supply chain"),
    Employer("Moët Hennessy (LVMH)", ("moet hennessy", "lvmh", "모엣헤네시"), "eu_fmcg", "FR",
             "European HQ supply chain"),
    Employer("AB InBev / Oriental Brewery", ("ab inbev", "anheuser busch inbev", "oriental brewery",
                                            "오비맥주"), "eu_fmcg", "BE", "Belgian HQ procurement"),
    Employer("Philip Morris International", ("philip morris", "pmi", "한국필립모리스", "필립모리스"),
             "eu_fmcg", "CH", "Lausanne operations centre, global leaf and materials sourcing"),
    Employer("British American Tobacco", ("british american tobacco", "bat", "bat rothmans",
                                          "브리티쉬아메리칸토바코"), "eu_fmcg", "GB", "Global sourcing incl. CIS"),
    Employer("JTI", ("jti", "japan tobacco international", "제이티아이"), "eu_fmcg", "CH",
             "Geneva HQ, CIS markets"),

    # --- European chemicals, ingredients, packaging -----------------------
    Employer("BASF", ("basf", "바스프", "한국바스프"), "eu_chem", "DE", "German HQ procurement"),
    Employer("Merck KGaA", ("merck", "머크"), "eu_chem", "DE", "German HQ procurement"),
    Employer("Evonik", ("evonik", "에보니크"), "eu_chem", "DE", "German HQ procurement"),
    Employer("Covestro", ("covestro", "코베스트로"), "eu_chem", "DE", "German HQ procurement"),
    Employer("Lanxess", ("lanxess", "랑세스"), "eu_chem", "DE", "German HQ procurement"),
    Employer("Wacker Chemie", ("wacker", "바커"), "eu_chem", "DE", "German HQ procurement"),
    Employer("Bayer", ("bayer", "바이엘"), "eu_chem", "DE", "German HQ procurement"),
    Employer("Symrise", ("symrise", "심라이즈"), "eu_chem", "DE", "German HQ raw-material sourcing"),
    Employer("Givaudan", ("givaudan", "지보단"), "eu_chem", "CH", "Swiss HQ raw-material sourcing"),
    Employer("dsm-firmenich", ("dsm firmenich", "dsm", "firmenich", "피르메니히"), "eu_chem", "CH",
             "European HQ raw-material sourcing"),
    Employer("Sika", ("sika", "한국시카"), "eu_chem", "CH", "Swiss HQ procurement"),
    Employer("Tetra Pak", ("tetra pak", "테트라팩"), "eu_chem", "CH", "European packaging supply chain"),
    Employer("SIG Group", ("sig group", "sig combibloc", "에스아이지"), "eu_chem", "CH",
             "European packaging supply chain"),

    # --- European logistics ---------------------------------------------
    Employer("DHL Group", ("dhl", "디에이치엘"), "eu_logistics", "DE", "Europe-Asia supply chain"),
    Employer("DB Schenker", ("db schenker", "schenker", "쉥커"), "eu_logistics", "DE",
             "Europe-Asia and Eurasian rail logistics"),
    Employer("DSV", ("dsv",), "eu_logistics", "DK", "Europe-Asia logistics"),
    Employer("Kuehne+Nagel", ("kuehne nagel", "퀴네앤드나겔"), "eu_logistics", "CH", "Europe-Asia logistics"),
    Employer("Hellmann Worldwide Logistics", ("hellmann",), "eu_logistics", "DE", "Europe-Asia logistics"),
    Employer("Maersk", ("maersk", "a.p. moller", "머스크"), "eu_logistics", "DK", "Europe-Asia logistics"),
    Employer("CEVA Logistics", ("ceva",), "eu_logistics", "FR", "Europe-Asia logistics"),

    # --- European industrial and automotive (international purchasing offices)
    Employer("Siemens", ("siemens", "지멘스", "한국지멘스"), "eu_industrial", "DE", "Korean supplier sourcing for Europe"),
    Employer("Bosch", ("bosch", "보쉬"), "eu_industrial", "DE", "Korean supplier sourcing for Europe"),
    Employer("Continental", ("continental", "콘티넨탈"), "eu_industrial", "DE", "Korean supplier sourcing for Europe"),
    Employer("ZF", ("zf", "zf friedrichshafen"), "eu_industrial", "DE", "Korean supplier sourcing for Europe"),
    Employer("Schaeffler", ("schaeffler", "셰플러"), "eu_industrial", "DE", "Korean supplier sourcing for Europe"),
    Employer("Mercedes-Benz", ("mercedes benz", "메르세데스벤츠"), "eu_industrial", "DE", "German HQ supply chain"),
    Employer("BMW Group", ("bmw", "비엠더블유"), "eu_industrial", "DE", "German HQ supply chain"),
    Employer("Volkswagen Group", ("volkswagen", "폭스바겐", "audi"), "eu_industrial", "DE", "German HQ supply chain"),

    # --- Apparel sourcing offices in Seoul --------------------------------
    Employer("adidas", ("adidas", "아디다스"), "apparel_sourcing", "DE", "Raw-material sourcing for global supply"),
    Employer("Puma", ("puma", "푸마"), "apparel_sourcing", "DE", "Raw-material sourcing for global supply"),
    Employer("H&M", ("h&m", "hennes mauritz"), "apparel_sourcing", "SE", "Production sourcing for Europe"),
    Employer("Nike", ("nike", "나이키"), "apparel_sourcing", "US", "Material sourcing for global supply"),

    # --- Korean groups with Europe / CIS supply chains --------------------
    Employer("Orion", ("orion corp", "orion", "오리온"), "kr_global", "KR", "Plants in Russia, CIS supply chain"),
    Employer("Lotte Wellfood", ("lotte wellfood", "롯데웰푸드", "lotte confectionery", "롯데제과"),
             "kr_global", "KR", "Plants in Russia and Kazakhstan"),
    Employer("KT&G", ("kt&g", "케이티앤지"), "kr_global", "KR", "Plant in Russia, CIS markets"),
    Employer("Paldo / HY", ("paldo", "팔도", "hy", "에이치와이", "korea yakult", "한국야쿠르트"),
             "kr_global", "KR", "Doshirak business in Russia"),
    Employer("CJ CheilJedang", ("cj cheiljedang", "cj제일제당", "씨제이제일제당", "cj corporation"),
             "kr_global", "KR", "European production and export"),
    Employer("Nongshim", ("nongshim", "농심"), "kr_global", "KR", "European export"),
    Employer("Samyang Foods", ("samyang foods", "삼양식품"), "kr_global", "KR", "European and CIS export"),
    Employer("Amorepacific", ("amorepacific", "아모레퍼시픽"), "kr_global", "KR",
             "Global packaging and raw-material sourcing"),
    Employer("LG H&H", ("lg household", "lg h&h", "lg생활건강"), "kr_global", "KR",
             "Global packaging and raw-material sourcing"),
    Employer("Cosmax", ("cosmax", "코스맥스"), "kr_global", "KR", "Global packaging and chemical sourcing"),
    Employer("Kolmar Korea", ("kolmar korea", "kolmar", "한국콜마"), "kr_global", "KR",
             "Global packaging and chemical sourcing"),
    Employer("POSCO International", ("posco international", "포스코인터내셔널"), "kr_global", "KR",
             "Trading and sourcing in Central Asia"),
    Employer("LX International", ("lx international", "lx인터내셔널"), "kr_global", "KR",
             "Trading and resources in Central Asia"),
    Employer("Hyundai Glovis", ("hyundai glovis", "현대글로비스"), "kr_global", "KR",
             "Europe and Eurasia logistics"),
)

# Legal-form and filler tokens dropped before comparing employer names.
_LEGAL_TOKENS = frozenset({
    "co", "corp", "corporation", "company", "inc", "incorporated", "ltd", "limited",
    "llc", "plc", "gmbh", "ag", "se", "sa", "nv", "bv", "kgaa", "holdings", "group",
    "korea", "the", "주식회사", "유한회사", "유한책임회사", "주", "유",
})


def normalise_company(name: str | None) -> tuple[str, ...]:
    """Tokens of a company name without legal forms, accents or punctuation."""
    text = unicodedata.normalize("NFKD", html.unescape(name or "").casefold())
    # Recompose so Hangul syllables (decomposed by NFKD) compare as written.
    text = unicodedata.normalize("NFC", "".join(ch for ch in text if not unicodedata.combining(ch)))
    text = re.sub(r"['’.]", "", text)
    tokens = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split()
    return tuple(token for token in tokens if token not in _LEGAL_TOKENS)


@lru_cache(maxsize=1)
def _alias_index() -> tuple[tuple[tuple[str, ...], Employer], ...]:
    pairs = [
        (normalise_company(alias), employer)
        for employer in EMPLOYERS
        for alias in (employer.name, *employer.aliases)
    ]
    # Longest alias first so "lotte wellfood" wins over a shorter prefix.
    return tuple(sorted((p for p in pairs if p[0]), key=lambda p: -len(p[0])))


def match_employer(company_name: str | None) -> Employer | None:
    """Whitelisted employer whose alias starts the company name, token-wise.

    "DHL Supply Chain Korea" matches "dhl"; "Bath & Body Works" does not
    match "bat" because whole tokens are compared.
    """
    tokens = normalise_company(company_name)
    if not tokens:
        return None
    for alias, employer in _alias_index():
        if tokens[: len(alias)] == alias:
            return employer
    return None


# ---------------------------------------------------------------------------
# Language and status gates
# ---------------------------------------------------------------------------

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
# Share of Hangul among Hangul + Latin letters above which the posting is
# treated as written in Korean. English postings that merely name a Korean
# entity stay near 0; Korean postings measured 0.4+.
HANGUL_SHARE_LIMIT = 0.2

_PLUS = re.compile(
    r"\b(?:plus|preferred|prefer|advantage\w*|asset|nice[- ]to[- ]have|bonus|desirable|"
    r"beneficial|optional|not required|not necessary)\b|우대"
)
_KOREAN_REQUIRED = (
    re.compile(
        r"\b(?:fluen\w*|native|bilingual|proficien\w*|business[- ]level|professional[- ]level)"
        r"\W+(?:\w+\W+){0,2}?korean\b"
    ),
    re.compile(
        r"\bkorean\W+(?:\w+\W+){0,1}?(?:language|speak\w*|spoken|written|verbal|fluen\w*|native|"
        r"proficien\w*|skills?)\b"
    ),
    re.compile(r"\b(?:korean and english|english and korean)\b"),
    re.compile(r"\btopik\b"),
    re.compile(r"한국어\s*(?:능통|원어민|필수|가능|유창|비즈니스|회화)|원어민\s*수준"),
)
_STATUS_RESTRICTED = (
    # "F-2 / F-4 / F-5 / F-6 visa holders only"; "F5" alone is also a vendor.
    re.compile(
        r"\bf-?[2456]\b(?:\s*[,/]\s*f-?[2456]\b)*\s*(?:visa|비자)[^.;\n]{0,30}"
        r"\b(?:only|required|must|mandatory)\b"
    ),
    re.compile(r"\bonly\b[^.;\n]{0,30}\bf-?[2456]\b(?:\s*[,/]\s*f-?[2456]\b)*\s*(?:visa|비자)"),
    re.compile(r"\bmust (?:currently |already )?(?:reside|be based|live|be located) in (?:south )?korea"),
    re.compile(r"\bkorean (?:nationals?|citizens?|nationality)\b[^.;\n]{0,20}\bonly\b"),
    re.compile(r"\bonly\b[^.;\n]{0,20}\bkorean (?:nationals?|citizens?)\b"),
    re.compile(
        r"\b(?:no|not able to (?:provide|offer)|unable to (?:provide|offer)|cannot (?:provide|offer)) "
        r"(?:visa |work permit )?sponsorship\b"
    ),
    re.compile(r"\bvisa sponsorship is not (?:available|provided|offered)\b"),
)
_SENTENCE_SPLIT = re.compile(r"[.;\n•·▪●]+")


def _plain_text(value: str | None) -> str:
    text = html.unescape(value or "")
    return re.sub(r"<[^>]+>", " ", text)


def hangul_share(text: str) -> float:
    hangul = len(_HANGUL.findall(text))
    latin = len(_LATIN.findall(text))
    return hangul / (hangul + latin) if hangul + latin else 0.0


def requires_korean(text: str) -> bool:
    """An explicit Korean requirement that is not phrased as a plus."""
    for sentence in _SENTENCE_SPLIT.split(text.casefold()):
        if any(p.search(sentence) for p in _KOREAN_REQUIRED) and not _PLUS.search(sentence):
            return True
    return False


def restricts_status(text: str) -> bool:
    lowered = text.casefold()
    return any(p.search(lowered) for p in _STATUS_RESTRICTED)


def rejection_reason(title: str, company_name: str | None, description: str | None) -> str | None:
    """Why a Korean vacancy fails the whitelist policy, or None if it passes."""
    if match_employer(company_name) is None:
        return "employer_not_whitelisted"
    text = _plain_text(description) or title
    if hangul_share(text) > HANGUL_SHARE_LIMIT:
        return "korean_language_posting"
    if requires_korean(text):
        return "korean_required"
    if restricts_status(text):
        return "residence_status_restricted"
    return None
