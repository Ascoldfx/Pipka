from app.api.auth import _verified_google_identity


def test_google_identity_requires_verified_email():
    assert _verified_google_identity(
        {"sub": "subject", "email": "user@example.com", "email_verified": True}
    ) == ("subject", "user@example.com")
    assert _verified_google_identity(
        {"sub": "subject", "email": "user@example.com", "email_verified": False}
    ) is None
    assert _verified_google_identity(
        {"sub": "subject", "email": "user@example.com"}
    ) is None


def test_google_identity_requires_subject_and_email():
    assert _verified_google_identity({"email": "user@example.com", "email_verified": True}) is None
    assert _verified_google_identity({"sub": "subject", "email_verified": True}) is None
