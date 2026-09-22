import time
import unittest
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from gateway.access import CloudflareAccessVerifier


class StaticJWKClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self.public_key)


class AccessVerifierTests(unittest.TestCase):
    def setUp(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.team = "example.cloudflareaccess.com"
        self.audience = "a" * 64
        self.email = "owner@example.com"
        self.verifier = CloudflareAccessVerifier(
            self.team,
            self.audience,
            self.email,
            StaticJWKClient(self.private_key.public_key()),
        )

    def token(self, **overrides):
        now = int(time.time())
        claims = {
            "iss": f"https://{self.team}",
            "aud": self.audience,
            "sub": "owner-identity",
            "email": self.email,
            "iat": now - 1,
            "exp": now + 300,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def test_valid_owner_token(self):
        identity = self.verifier.verify(self.token())
        self.assertEqual(identity.email, self.email)

    def test_wrong_identity_is_rejected(self):
        self.assertIsNone(self.verifier.verify(self.token(email="other@example.com")))

    def test_wrong_audience_is_rejected(self):
        self.assertIsNone(self.verifier.verify(self.token(aud="wrong")))

    def test_expired_token_is_rejected(self):
        self.assertIsNone(self.verifier.verify(self.token(exp=int(time.time()) - 1)))


if __name__ == "__main__":
    unittest.main()
