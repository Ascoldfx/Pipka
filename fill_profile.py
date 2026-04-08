"""One-time script to populate user profile from CV data."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.models import Base
from app.models.user import User, UserProfile

DB_PATH = "sqlite:///./jobhunt.db"
engine = create_engine(DB_PATH)
Base.metadata.create_all(engine)

RESUME_TEXT = """Anton Gotskyi — Operations & Supply Chain Manager
16+ years progressive experience at Solomiya LLC (top-5 Ukrainian FMCG/tea manufacturer).
4 years as COO leading 60+ employees across Production, Logistics, Procurement.

Key Achievements:
- 18% operating cost reduction (COO, 2022)
- 25% delivery lead time improvement (2018)
- 15% procurement cost reduction (post-war recovery)
- Rebuilt full supply chain from zero in 4 months after total factory destruction
- Zero stockouts during active conflict

Career: Marketing Specialist → International Procurement Manager → Head of SCM → COO → Senior Procurement Manager

Current: Senior Procurement Manager (remote, Leipzig-based since 2023)
- Full-cycle procurement: raw materials, capital equipment, machinery, packaging lines
- Tender management: public & corporate, documentation, supplier selection, contract award
- Renegotiated EU & Asian supplier contracts: -15% procurement costs
- AI-assisted workflows (Claude, ChatGPT) for RFQ automation: -30% sourcing cycle time

COO (2019-2022): P&L for Production, Logistics, Procurement, Admin. €10M/year procurement volume.
Crisis management 2022: maintained operations under armed conflict.

Skills: Strategic Sourcing, Vendor Management, Contract Negotiation, Incoterms 2020,
S&OP, Demand Planning, Category Management, Source-to-Pay, ERP (SAP MM/SD, 1C WMS/ERP),
Power BI, Advanced Excel, AI & Automation.

Education: MSc Human Resource Management & Marketing, KNEU Ukraine 2012.
German B1→B2 (Volkshochschule Leipzig, ongoing).
Work authorisation: §24 AufenthG — full, unrestricted. No sponsorship required.
Driving licence B. Open to relocation within DACH."""

with Session(engine) as session:
    # Get first user or create one
    user = session.execute(select(User)).scalar_one_or_none()
    if not user:
        user = User(telegram_id=143204964, name="Anton Gotskyi")
        session.add(user)
        session.flush()
        print(f"Created user id={user.id}")
    else:
        print(f"Found user id={user.id}, telegram_id={user.telegram_id}")

    # Get or create profile
    profile = session.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).scalar_one_or_none()

    if not profile:
        profile = UserProfile(user_id=user.id)
        session.add(profile)

    profile.resume_text = RESUME_TEXT
    profile.target_titles = [
        "Supply Chain Manager", "Procurement Manager", "Head of Procurement",
        "Head of Supply Chain", "Operations Manager", "Leiter Einkauf",
        "Leiter Supply Chain", "Director Supply Chain", "VP Procurement",
        "COO", "Einkaufsleiter", "Logistik Manager",
    ]
    profile.min_salary = 55000
    profile.max_commute_km = 100
    profile.languages = {"en": "C1", "de": "B1", "uk": "Native", "ru": "Native"}
    profile.experience_years = 16
    profile.industries = ["FMCG", "Manufacturing", "Logistics", "Automotive", "Retail", "Pharma", "Food & Beverage"]
    profile.work_mode = "any"
    profile.preferred_countries = ["de", "at", "nl"]
    profile.base_location = "Leipzig"

    session.commit()
    print("Profile filled!")
    print(f"Titles: {profile.target_titles}")
    print(f"Languages: {profile.languages}")
    print(f"Location: {profile.base_location}")
    print("Done! Now run: python run.py")
