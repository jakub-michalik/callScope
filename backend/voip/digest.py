"""SIP/HTTP Digest authentication (RFC 2617) — pure, unit-tested."""
from __future__ import annotations
import hashlib
import re


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def response(method: str, uri: str, username: str, password: str, realm: str,
             nonce: str, qop: str | None = None,
             nc: str = "00000001", cnonce: str = "0a4f113b") -> str:
    ha1 = _md5(f"{username}:{realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")
    if qop:
        return _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return _md5(f"{ha1}:{nonce}:{ha2}")


def parse_challenge(header: str) -> dict:
    """Parse a WWW-Authenticate / Proxy-Authenticate Digest header into params."""
    out = {}
    for m in re.finditer(r'(\w+)\s*=\s*("([^"]*)"|[^,\s]+)', header):
        out[m.group(1)] = m.group(3) if m.group(3) is not None else m.group(2)
    return out


def authorization(method: str, uri: str, username: str, password: str,
                  challenge: dict, nc: str = "00000001", cnonce: str = "0a4f113b") -> str:
    """Build an Authorization header value from a parsed challenge."""
    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = challenge.get("qop")
    if qop and "," in qop:
        qop = "auth" if "auth" in qop.split(",") else qop.split(",")[0]
    resp = response(method, uri, username, password, realm, nonce, qop, nc, cnonce)
    parts = [f'username="{username}"', f'realm="{realm}"', f'nonce="{nonce}"',
             f'uri="{uri}"', f'response="{resp}"', "algorithm=MD5"]
    if qop:
        parts += [f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"']
    if challenge.get("opaque"):
        parts.append(f'opaque="{challenge["opaque"]}"')
    return "Digest " + ", ".join(parts)
