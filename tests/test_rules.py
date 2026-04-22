import pytest
from app.models.job import Job
from app.models.user import UserProfile
from app.scoring.rules import pre_filter

def test_hard_reject_junior():
    job = Job(title="Junior Procurement Analyst", description="Some text")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "low"

def test_hard_reject_foreign_language():
    job = Job(title="Director of Supply Chain", description="fluent french required for this role")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "low"

def test_user_exclusions():
    profile = UserProfile(excluded_keywords=["apple", "amazon"])
    job = Job(title="Head of Procurement", description="Join Amazon team")
    passed, bucket = pre_filter(job, profile)
    assert passed is False
    assert bucket == "low"

def test_english_only_filter_fail():
    profile = UserProfile(english_only=True)
    job = Job(title="Leiter Logistik", description="Wir suchen einen Leiter.")
    passed, bucket = pre_filter(job, profile)
    assert passed is False
    assert bucket == "low"

def test_english_only_filter_pass():
    profile = UserProfile(english_only=True)
    job = Job(title="Director Supply Chain", description="International team, english working language.")
    passed, bucket = pre_filter(job, profile)
    assert passed is True

def test_domain_check_fail():
    # Marketing director should fail domain check
    job = Job(title="Marketing Director", description="Responsible for campaigns")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "low"

def test_director_seniority_pass():
    job = Job(title="Director of Global Sourcing", description="Manage global sourcing strategy")
    passed, bucket = pre_filter(job, None)
    assert passed is True
    assert bucket == "high"

def test_plain_manager_fail():
    job = Job(title="Procurement Manager", description="Manage procurement tasks")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "low"

def test_salary_floor():
    profile = UserProfile(min_salary=100000)
    job_low = Job(title="Head of Logistics", description="Logistics", salary_min=50000)
    passed_low, _ = pre_filter(job_low, profile)
    assert passed_low is False
    
    job_high = Job(title="Head of Logistics", description="Logistics", salary_min=90000)
    passed_high, _ = pre_filter(job_high, profile)
    assert passed_high is True
