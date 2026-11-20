"""Where the VAN API key comes from.

Order: `VAN_API_KEY` in the environment, then `VAN_API_KEY_SECRET`, a Google
Secret Manager version name such as
`projects/my-proj/secrets/van-api-key/versions/latest`. The key is held in a
`SecretStr`-style wrapper so it can't end up in a repr, a log line or a
traceback by accident.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping


class ApiKey:
    """The API key with its database mode suffix, hidden from str() and repr()."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "ApiKey(****)"

    __str__ = __repr__


class MissingKeyError(RuntimeError):
    pass


def with_mode(raw: str, database_mode: int) -> str:
    """Add the `|0` / `|1` suffix VAN expects, or check the one already there."""
    raw = raw.strip()
    if "|" in raw:
        key, _, mode = raw.rpartition("|")
        if mode not in ("0", "1"):
            raise MissingKeyError("the API key's database mode suffix must be |0 or |1")
        if int(mode) != database_mode:
            raise MissingKeyError(
                f"the API key ends in |{mode} but the config asks for database mode {database_mode}"
            )
        return f"{key}|{mode}"
    return f"{raw}|{database_mode}"


def _secret_manager_fetch(name: str) -> str:  # pragma: no cover - needs Google credentials
    try:
        from google.cloud import secretmanager
    except ImportError as exc:
        raise MissingKeyError(
            "VAN_API_KEY_SECRET is set but google-cloud-secret-manager isn't installed; "
            "install with `uv sync --extra gcp`"
        ) from exc
    client = secretmanager.SecretManagerServiceClient()
    return client.access_secret_version(name=name).payload.data.decode("utf-8")


def load_api_key(
    database_mode: int,
    env: Mapping[str, str] | None = None,
    fetch_secret: Callable[[str], str] = _secret_manager_fetch,
) -> ApiKey:
    env = os.environ if env is None else env
    raw = env.get("VAN_API_KEY")
    if not raw and env.get("VAN_API_KEY_SECRET"):
        raw = fetch_secret(env["VAN_API_KEY_SECRET"])
    if not raw:
        raise MissingKeyError(
            "no VAN API key: set VAN_API_KEY, or VAN_API_KEY_SECRET to a Secret Manager version, "
            "or run with --demo to use the built-in fake data"
        )
    return ApiKey(with_mode(raw, database_mode))
