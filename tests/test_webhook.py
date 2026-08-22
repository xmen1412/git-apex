from __future__ import annotations

import hashlib
import hmac

from services.webhook import verify_signature


def test_verify_signature_valid():
    secret = "mysecret"
    payload = b'{"commits":[]}'
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert verify_signature(payload, sig, secret) is True


def test_verify_signature_invalid():
    secret = "mysecret"
    payload = b'{"commits":[]}'
    assert verify_signature(payload, "sha256=bad", secret) is False


def test_verify_signature_missing():
    assert verify_signature(b"{}", None, "secret") is False


def test_verify_signature_wrong_prefix():
    assert verify_signature(b"{}", "sha1=abc", "secret") is False
