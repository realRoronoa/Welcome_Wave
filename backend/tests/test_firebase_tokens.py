"""Firebase ID tokens are verified against Google's public keys, without Google Cloud credentials (app/firebase_tokens.py).

A local RSA key and self-signed certificate stand in for Google's securetoken keys, so these tests need no network."""
from __future__ import annotations

import datetime as dt
import time

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.firebase_tokens import InvalidToken, _Certs, verify

PROJECT = "welcome-wave"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken.test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, CERT = _keypair()
OTHER_KEY, _ = _keypair()


def certs_for(mapping, calls=None):
    def fetch():
        if calls is not None:
            calls.append(1)
        return dict(mapping), 3600.0
    return _Certs(fetch)


def token(key=KEY, kid="k1", alg="RS256", **over):
    now = int(time.time())
    claims = {"iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT, "sub": "uid-1", "auth_time": now - 5,
              "iat": now - 5, "exp": now + 3600, "email": "a@b.dev", "email_verified": True}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key if alg == "RS256" else "secret", algorithm=alg, headers={"kid": kid})


def test_a_valid_token_gives_its_claims_and_uid():
    claims = verify(token(), PROJECT, certs_for({"k1": CERT}))
    assert claims["uid"] == "uid-1" and claims["email"] == "a@b.dev" and claims["email_verified"] is True


@pytest.mark.parametrize("over", [
    {"aud": "another-project"},
    {"iss": "https://securetoken.google.com/another-project"},
    {"exp": int(time.time()) - 120},
    {"sub": ""},
    {"auth_time": int(time.time()) + 600},
])
def test_wrong_project_issuer_expiry_subject_or_future_sign_in_is_refused(over):
    with pytest.raises(InvalidToken):
        verify(token(**over), PROJECT, certs_for({"k1": CERT}))


def test_a_token_signed_by_another_key_is_refused():
    with pytest.raises(InvalidToken):
        verify(token(key=OTHER_KEY), PROJECT, certs_for({"k1": CERT}))


def test_only_rs256_is_accepted():
    with pytest.raises(InvalidToken):
        verify(token(alg="HS256"), PROJECT, certs_for({"k1": CERT}))


def test_an_unknown_key_id_refetches_once_then_refuses():
    calls = []
    certs = certs_for({"k1": CERT}, calls)
    verify(token(), PROJECT, certs)
    verify(token(), PROJECT, certs)
    assert len(calls) == 1  # cached
    with pytest.raises(InvalidToken):
        verify(token(kid="rotated"), PROJECT, certs)
    assert len(calls) == 2  # one refetch for the unknown key


def test_no_project_configured_is_refused():
    with pytest.raises(InvalidToken):
        verify(token(), "", certs_for({"k1": CERT}))


def test_garbage_is_refused():
    with pytest.raises(InvalidToken):
        verify("not-a-token", PROJECT, certs_for({"k1": CERT}))
