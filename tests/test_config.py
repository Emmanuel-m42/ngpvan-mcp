from __future__ import annotations

import pytest

from ngpvan_mcp.config import Config, ConfigError
from ngpvan_mcp.secrets import MissingKeyError, load_api_key, with_mode


def test_defaults_are_read_only_and_initials():
    c = Config()
    assert c.names == "initial"
    assert not c.write_enabled("apply_canvass_response")


def test_loads_example_file():
    c = Config.load("ngpvan-mcp.example.toml")
    assert not c.write_enabled("apply_canvass_response")
    assert c.limits.requests_per_second > 0


def test_api_key_in_config_is_refused():
    with pytest.raises(ConfigError, match="VAN_API_KEY"):
        Config.from_mapping({"van": {"api_key": "abc|1"}})


def test_unknown_write_tool_is_refused():
    with pytest.raises(ConfigError, match="delete_person"):
        Config.from_mapping({"writes": {"delete_person": True}})


def test_bad_policy_and_mode():
    with pytest.raises(ConfigError):
        Config.from_mapping({"privacy": {"names": "everything"}})
    with pytest.raises(ConfigError):
        Config.from_mapping({"van": {"database_mode": 2}})


def test_with_mode_adds_or_checks_suffix():
    assert with_mode("abc", 1) == "abc|1"
    assert with_mode("abc|0", 0) == "abc|0"
    with pytest.raises(MissingKeyError, match="database mode"):
        with_mode("abc|0", 1)
    with pytest.raises(MissingKeyError):
        with_mode("abc|7", 1)


def test_key_from_env():
    assert load_api_key(1, env={"VAN_API_KEY": "abc"}).reveal() == "abc|1"


def test_key_from_secret_manager():
    seen = []

    def fetch(name):
        seen.append(name)
        return "from-secret"

    name = "projects/p/secrets/van-api-key/versions/latest"
    key = load_api_key(1, env={"VAN_API_KEY_SECRET": name}, fetch_secret=fetch)
    assert key.reveal() == "from-secret|1" and seen == [name]


def test_missing_key_points_to_demo():
    with pytest.raises(MissingKeyError, match="--demo"):
        load_api_key(1, env={})
