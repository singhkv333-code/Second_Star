"""Inbound webhook receivers for the Phase-7 push transports.

Each module here owns one provider's signature scheme + payload
shape. Today: Miniflux (HMAC-SHA256). Future: changedetection.io
(Apprise/JSON), n8n, RSSHub-push, etc.
"""
from __future__ import annotations
