"""Every email template: renders, escapes, links safely, and keeps the design rules."""
import re

import pytest

import mail_kit
import mail_templates as t
import mailer

ALL = t.samples()


@pytest.mark.parametrize("kind", sorted(ALL))
def test_renders_both_parts_with_the_footer(kind):
    mail = ALL[kind]
    assert mail.kind == kind and mail.subject and "\n" not in mail.subject
    assert mail.html.startswith("<!DOCTYPE html>") and "</html>" in mail.html
    assert "<" not in mail.text.replace("<-", "")
    assert "contact@pivotnow.in" in mail.html and "contact@pivotnow.in" in mail.text
    assert "info@pivotnow.in" not in mail.html


@pytest.mark.parametrize("kind", sorted(ALL))
def test_design_rules(kind):
    html = re.sub(r"<style>.*?</style>", "", ALL[kind].html, flags=re.S)  # dark-mode rules aside
    sizes = [int(s) for s in re.findall(r"font-size:(\d+)px", html) if s != "1"]
    assert min(sizes) >= 14, "no small print"
    # No pale grey text: every text colour on the light palette is dark.
    for colour in re.findall(r"(?<![-\w])color:(#[0-9a-fA-F]{6})", html):
        if colour.lower() in ("#f3f0e7", "#fffdf8", "#dcd8cd", "#2b9bb1"):
            continue                    # hero/button text on ink, preheader, brand dot
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        assert 0.2126 * r + 0.7152 * g + 0.0722 * b < 110, colour
    for href in re.findall(r'href="([^"]+)"', html):
        assert href.startswith(("https://", "mailto:")), href


def test_values_are_escaped_and_subjects_single_line():
    mail = t.alert_triggered(symbol="X<script>", condition="a\r\nBcc: evil@example.com",
                             value="1", level="2", interval="1m", when="now", alert_id=1)
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html
    assert "\n" not in mail.subject and "\r" not in mail.subject


def test_links_must_be_https():
    with pytest.raises(ValueError):
        mail_kit.button("Go", "javascript:alert(1)")


def test_product_mail_carries_unsubscribe_and_security_does_not():
    cfg = mailer.Config(True, "contact@pivotnow.in", "pw")
    product = mailer.build(ALL["weekly_summary"], "a@example.com", cfg, "ref-1")
    security = mailer.build(ALL["password_reset"], "a@example.com", cfg, "ref-2")
    assert product["List-Unsubscribe"].startswith("<mailto:contact@pivotnow.in")
    assert security["List-Unsubscribe"] is None
    assert security["From"] == "Pivot <contact@pivotnow.in>"
    assert security["Message-ID"] == "<ref-2@pivotnow.in>"


def test_config_defaults_to_hostinger_and_contact(monkeypatch):
    for k in ("PIVOT_MAIL_HOST", "PIVOT_SMTP_HOST", "PIVOT_MAIL_FROM", "PIVOT_MAIL_USER",
              "PIVOT_SMTP_USER"):
        monkeypatch.delenv(k, raising=False)
    cfg = mailer.Config.from_env()
    assert (cfg.host, cfg.sender, cfg.username, cfg.port) == (
        "smtp.hostinger.com", "contact@pivotnow.in", "contact@pivotnow.in", 465)
