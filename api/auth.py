"""Who is calling? Cloudflare Access in production, a fixed developer identity locally.

In production every request reaches us through the Cloudflare Tunnel, after Cloudflare Access has logged the user
in. Access adds a signed JWT (header `Cf-Access-Jwt-Assertion`). We verify its signature against Cloudflare's
public keys and check the audience (our Access application) and issuer (our team domain). We do NOT trust the
plain `Cf-Access-Authenticated-User-Email` header: anything that reached the app without going through Access
(e.g. another container on the host) could set it.
"""
import functools

import jwt
from fastapi import HTTPException, Request

from reviewer_loop import config

DEV_USER = "dev@local"


@functools.cache
def _jwks_client(team_domain: str) -> jwt.PyJWKClient:
    # Cloudflare rotates these keys; PyJWKClient caches them and refetches when a token names an unknown key.
    return jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)


def current_user(request: Request) -> str:
    settings = config.get_settings()
    if not settings.production:
        return DEV_USER
    token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
    if not token:
        raise HTTPException(401, "Not signed in through Cloudflare Access")
    try:
        key = _jwks_client(settings.cf_access_team_domain).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.cf_access_aud,
                            issuer=f"https://{settings.cf_access_team_domain}")
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid Cloudflare Access token ({type(exc).__name__})") from None
    return claims.get("email") or claims["sub"]
