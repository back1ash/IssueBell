from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_landing_focuses_on_features_and_diverse_starter_packs() -> None:
    html = read("app/templates/index.html")
    javascript = read("static/app.js")

    assert "within minutes" in html
    assert "about every 3 minutes" in html
    for feature in (
        "Choose real repository labels",
        "Alert on actionable changes",
        "Test and monitor delivery",
    ):
        assert feature in html
    for repository in (
        "freeCodeCamp/freeCodeCamp",
        "scikit-learn/scikit-learn",
        "mdn/content",
        "microsoft/vscode",
    ):
        assert repository in html
        assert repository in javascript
    assert "Founder case" not in html
    assert "Founder-tested" not in html
    assert "138149" not in html
    assert "3862" not in html
    assert "issuebell.app" not in html


def test_dashboard_targets_reliability_endpoints_with_csrf() -> None:
    html = read("app/templates/index.html")
    javascript = read("static/app.js")

    assert 'name="csrf-token"' in html
    assert "/subscriptions/repositories/" in javascript
    assert 'fetch("/subscriptions/test-dm"' in javascript
    assert 'fetch("/subscriptions/status"' in javascript
    assert '"X-CSRF-Token"' in javascript
    assert "manual" in javascript.lower()


def test_account_mutations_are_post_forms() -> None:
    html = read("app/templates/index.html")

    for route in ("/auth/logout", "/auth/github/disconnect", "/auth/delete-account"):
        assert f'method="post" action="{route}"' in html
    assert html.count('name="csrf_token"') >= 3


def test_destructive_actions_use_app_owned_confirmation_ui() -> None:
    html = read("app/templates/index.html")
    javascript = read("static/app.js")
    css = read("static/style.css")

    assert 'id="confirm-dialog"' in html
    assert 'id="app-toast"' in html
    assert "requestConfirmation" in javascript
    assert "showToast" in javascript
    assert "window.confirm" not in javascript
    assert "window.alert" not in javascript
    assert ".confirm-dialog::backdrop" in css
    assert ".app-toast" in css


def test_legal_pages_and_footer_are_linked_and_styled() -> None:
    html = read("app/templates/index.html")
    css = read("static/style.css")

    assert 'href="/privacy"' in html
    assert 'href="/terms"' in html
    for class_name in (".legal-page", ".legal-content", ".legal-kicker"):
        assert class_name in css


def test_public_pages_expose_complete_search_metadata() -> None:
    index = read("app/templates/index.html")
    privacy = read("app/templates/privacy.html")
    terms = read("app/templates/terms.html")
    robots = read("static/robots.txt")
    sitemap = read("static/sitemap.xml")

    assert 'rel="canonical" href="https://issuebell.com/"' in index
    assert 'name="twitter:image"' in index
    assert 'type="application/ld+json"' in index
    assert '"@type": "SoftwareApplication"' in index
    assert 'content="noindex, nofollow, noarchive"' in index
    assert 'rel="canonical" href="https://issuebell.com/privacy"' in privacy
    assert 'rel="canonical" href="https://issuebell.com/terms"' in terms
    assert "Sitemap: https://issuebell.com/sitemap.xml" in robots
    assert "Disallow: /auth/" in robots
    for url in (
        "https://issuebell.com/",
        "https://issuebell.com/privacy",
        "https://issuebell.com/terms",
    ):
        assert f"<loc>{url}</loc>" in sitemap
