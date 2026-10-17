"""SharePerks first, and never a placeholder dressed as a logo."""
from backend.market import company_logos as cl


def test_listed_ticker_resolves_to_its_isin_icon_without_redis_or_db(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("SharePerks hits must not touch Redis")
    monkeypatch.setattr(cl.redis_client, "get", boom)
    monkeypatch.setattr(cl.redis_client, "mget", boom)
    url = "https://company-logo.shareperks.in/logo/INE002A01018/icon.svg"
    assert cl.get_logo_url("reliance") == url
    assert cl.get_logo_urls(["RELIANCE", "M&M"])["RELIANCE"] == url
    assert cl.get_logo_urls(["M&M"])["M&M"].endswith("/INE101A01026/icon.svg")


def test_unlisted_ticker_gets_no_shareperks_url():
    """Its icon route answers 200 with a placeholder for an unknown ISIN."""
    assert cl.shareperks_logo_url("NOT-A-LISTING") is None
    assert cl.shareperks_logo_url("") is None


def test_logodev_urls_404_instead_of_a_generated_letter_tile(monkeypatch):
    monkeypatch.setattr(cl.settings, "logodev_publishable_token", "pk_test")
    assert cl.logo_url_for_domain("hal-india.co.in").endswith("&fallback=404")
