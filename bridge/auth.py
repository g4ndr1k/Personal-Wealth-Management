import hmac
from pathlib import Path


def load_token(token_file: Path) -> str:
    token = token_file.read_text().strip()
    if not token:
        raise RuntimeError("Bridge token file is empty")
    return token


def is_authorized(header_value: str, token: str) -> bool:
    if not header_value or not header_value.startswith("Bearer "):
        return False
    supplied = header_value[7:].strip()
    return hmac.compare_digest(supplied.encode(), token.encode())
