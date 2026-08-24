"""Integration coverage for app-wide security wiring."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models import Base, User
from app.token_crypto import TOKEN_PREFIX


def test_app_sets_security_headers_and_rejects_untrusted_hosts() -> None:
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"

    landing = client.get("/")
    assert landing.status_code == 200
    assert "Catch the right GitHub issue" in landing.text
    assert '<link rel="canonical" href="https://issuebell.com/"' in landing.text
    assert 'type="application/ld+json"' in landing.text

    privacy = client.get("/privacy")
    terms = client.get("/terms")
    assert privacy.status_code == 200
    assert terms.status_code == 200
    assert "https://issuebell.com/privacy" in privacy.text
    assert "https://issuebell.com/terms" in terms.text

    robots = client.get("/robots.txt")
    sitemap = client.get("/sitemap.xml")
    assert robots.status_code == 200
    assert robots.headers["content-type"].startswith("text/plain")
    assert "Sitemap: https://issuebell.com/sitemap.xml" in robots.text
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "<loc>https://issuebell.com/</loc>" in sitemap.text

    rejected = client.get("/healthz", headers={"Host": "attacker.example"})
    assert rejected.status_code == 400


def test_user_model_encrypts_github_token_at_rest() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        user = User(
            discord_id="integration-user",
            username="tester",
            github_token="gho_plaintext-token",
        )
        db.add(user)
        db.commit()
        user_id = user.id

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT github_token FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        ).scalar_one()
    assert stored.startswith(TOKEN_PREFIX)
    assert "gho_plaintext-token" not in stored

    with session_factory() as db:
        assert db.get(User, user_id).github_token == "gho_plaintext-token"
