"""Cloudflare Access JWT verification."""
from __future__ import annotations

import hmac
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient


@dataclass(frozen=True)
class AccessIdentity:
    email: str
    subject: str


class CloudflareAccessVerifier:
    def __init__(
        self,
        team_domain: str,
        audience: str,
        allowed_email: str,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        self.team_domain = team_domain.strip().lower()
        self.audience = audience.strip()
        self.allowed_email = allowed_email.strip().lower()
        certs_url = f"https://{self.team_domain}/cdn-cgi/access/certs"
        self.jwks_client = jwks_client or PyJWKClient(certs_url, cache_keys=True, lifespan=3600)

    def verify(self, token: str | None) -> AccessIdentity | None:
        if not token:
            return None
        try:
            key = self.jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=f"https://{self.team_domain}",
                options={"require": ["exp", "iat", "iss", "aud", "sub", "email"]},
            )
        except jwt.PyJWTError:
            return None
        email = str(claims.get("email", "")).strip().lower()
        subject = str(claims.get("sub", "")).strip()
        if not email or not subject or not hmac.compare_digest(email, self.allowed_email):
            return None
        return AccessIdentity(email=email, subject=subject)
