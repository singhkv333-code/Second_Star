"""Offline domain routing gates; no production connections or state writes."""
import importlib.util
from pathlib import Path
import unittest

FILE = Path(__file__).with_name("publish_app_domain.py")
spec = importlib.util.spec_from_file_location("app_domain", FILE)
domain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(domain)


class AppDomainTests(unittest.TestCase):
    def test_reuses_tls_routes_without_copying_old_redirect_or_other_servers(self):
        source = Path(__file__).with_name("nginx-charto.conf").read_text()
        rendered = domain.render(source)
        self.assertEqual(rendered.count("server {"), 2)
        self.assertNotIn(domain.OLD_DOMAIN, rendered)
        self.assertIn("server_name app.pivotnow.in;", rendered)
        self.assertIn("/etc/letsencrypt/live/app.pivotnow.in/fullchain.pem", rendered)
        self.assertIn("location ^~ /.well-known/acme-challenge/", rendered)
        self.assertIn("return 301 https://app.pivotnow.in$request_uri", rendered)

    def test_preserves_streaming_socket_auth_and_method_split_contracts(self):
        rendered = domain.render(Path(__file__).with_name("nginx-charto.conf").read_text())
        for contract in (
            "proxy_buffering off;", "proxy_set_header Upgrade $http_upgrade;",
            "location ~ ^/auth/(login|signup|reset)", "location @strategies_api",
            "if ($request_method = POST) { return 418; }", "location ^~ /chart-app/",
            "proxy_set_header Authorization $http_authorization;",
            "proxy_pass http://127.0.0.1:3000;", "proxy_pass http://127.0.0.1:5174;",
            "proxy_pass http://127.0.0.1:8000;",
        ):
            self.assertIn(contract, rendered)

    def test_rejects_a_different_host_or_non_platform_config(self):
        source = Path(__file__).with_name("nginx-charto.conf").read_text()
        for invalid in ("server { listen 80; }", source.replace(domain.OLD_DOMAIN, "example.com"),
                        source.replace("proxy_pass http://127.0.0.1:3000", "proxy_pass http://example.com")):
            with self.assertRaises(ValueError):
                domain.render(invalid)

    def test_certificate_directory_does_not_depend_on_previous_certbot_name(self):
        source = Path(__file__).with_name("nginx-charto.conf").read_text().replace(
            f"/etc/letsencrypt/live/{domain.OLD_DOMAIN}/", "/etc/letsencrypt/live/old-validation-name/")
        rendered = domain.render(source)
        self.assertNotIn("/old-validation-name/", rendered)
        self.assertIn(f"ssl_certificate /etc/letsencrypt/live/{domain.DOMAIN}/fullchain.pem;", rendered)
        self.assertIn(f"ssl_certificate_key /etc/letsencrypt/live/{domain.DOMAIN}/privkey.pem;", rendered)


if __name__ == "__main__":
    unittest.main()
