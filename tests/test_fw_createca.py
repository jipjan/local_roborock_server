"""Tests for the local ``.../fwapi/createca`` TURN route -- issue #18.

When a TURN server is configured the route answers the robot's camera TURN request
with a plain-JSON credential body; when it is not, the request falls through to the
catchall so behaviour is unchanged.
"""

from __future__ import annotations

import logging

# Importing the backend bridge puts the bundled_backend dir on sys.path so the
# bare ``shared`` / ``https_server`` imports inside the route modules resolve.
import roborock_local_server.backend as backend  # noqa: E402
from shared.context import ServerContext  # noqa: E402


def _make_context(tmp_path, *, turn_enabled: bool, createca_auth: str = "open"):
    return ServerContext(
        api_host="api.example.com",
        mqtt_host="mqtt.example.com",
        wood_host="wood.example.com",
        region="us",
        protocol_login_email="user@example.com",
        localkey="0123456789abcdef",
        duid="1234567890",
        mqtt_usr="mqtt_usr",
        mqtt_passwd="mqtt_passwd",
        mqtt_clientid="mqtt_clientid",
        https_port=443,
        mqtt_tls_port=8883,
        http_jsonl=tmp_path / "http.jsonl",
        mqtt_jsonl=tmp_path / "mqtt.jsonl",
        loggers={"real_stack": logging.getLogger("test.real_stack")},
        turn_enabled=turn_enabled,
        turn_host="192.168.1.10",
        turn_port=3478,
        turn_username="rrturn",
        turn_password="s3cret",
        turn_realm="turn.example.com",
        turn_ttl=86400,
        turn_createca_auth=createca_auth,
    )


def _resolve(ctx, path, body_params=None):
    return backend.resolve_route(
        rules=backend.default_endpoint_rules(),
        context=ctx,
        clean_path=path,
        query_params={},
        body_params=body_params if body_params is not None else {"did": ["1234567890"]},
        method="POST",
    )


def test_createca_returns_turn_payload_when_enabled(tmp_path):
    ctx = _make_context(tmp_path, turn_enabled=True)

    route_name, payload = _resolve(ctx, "/iot.roborock.com/fwapi/createca")

    assert route_name == "fw_createca"
    assert payload["code"] == 200
    assert payload["msg"] == "success"
    data = payload["data"]
    assert data["url"] == "turn:192.168.1.10:3478"
    assert data["user"] == "rrturn" and data["pwd"] == "s3cret"
    # ICE-style aliases also present for the firmware's alternate parser.
    assert data["username"] == "rrturn" and data["credential"] == "s3cret"
    assert data["realm"] == "turn.example.com"
    assert data["ttl"] == 86400
    assert "stun:192.168.1.10:3478" in data["urls"]
    # ``result`` mirrors ``data``.
    assert payload["result"] == data


def test_createca_matches_common_path_variants(tmp_path):
    ctx = _make_context(tmp_path, turn_enabled=True)
    for path in (
        "/iot.roborock.com/fwapi/createca",
        "/fwapi/createca",
        "/api/iot.roborock.com/fwapi/createca",
        "/iot.roborock.com/fwapi/createca/",
    ):
        route_name, payload = _resolve(ctx, path)
        assert route_name == "fw_createca", path
        assert payload["code"] == 200, path


def test_createca_falls_back_to_catchall_when_disabled(tmp_path):
    ctx = _make_context(tmp_path, turn_enabled=False)

    route_name, payload = _resolve(ctx, "/iot.roborock.com/fwapi/createca")

    # Route still matches, but with TURN disabled it must not emit credentials;
    # it falls back to the previous catchall response instead.
    assert route_name == "fw_createca"
    assert "data" not in payload or "url" not in payload.get("data", {})


# --- createca_auth = "signed" -------------------------------------------------

import base64  # noqa: E402

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

_DEVICE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_CREATECA = "/iot.roborock.com/fwapi/createca"


def _signed_body(did="1103842988936", key=_DEVICE_KEY):
    canonical = f"did={did}&pid=roborock.vacuum.a296&uid=1234567890"
    signature = key.sign(canonical.encode(), padding.PKCS1v15(), hashes.SHA256())
    return {
        "did": [did],
        "pid": ["roborock.vacuum.a296"],
        "uid": ["1234567890"],
        "signature": [base64.b64encode(signature).decode()],
    }


def _signed_context(tmp_path, monkeypatch):
    ctx = _make_context(tmp_path, turn_enabled=True, createca_auth="signed")
    keys = {"1103842988936": _DEVICE_KEY.public_key()}
    monkeypatch.setattr(ctx, "device_public_key", lambda did: keys.get(did))
    return ctx


def test_signed_mode_answers_a_robot_with_a_valid_signature(tmp_path, monkeypatch):
    ctx = _signed_context(tmp_path, monkeypatch)
    _, payload = _resolve(ctx, _CREATECA, _signed_body())
    assert payload["code"] == 200
    assert payload["data"]["pwd"] == "s3cret"


def _assert_refused(payload):
    assert payload["code"] == 401
    assert "s3cret" not in repr(payload)
    assert payload["data"] == {"url": "turn:192.168.1.10:3478", "signed": True}


def test_signed_mode_refuses_unsigned_requests(tmp_path, monkeypatch):
    ctx = _signed_context(tmp_path, monkeypatch)
    _assert_refused(_resolve(ctx, _CREATECA)[1])
    _assert_refused(_resolve(ctx, _CREATECA, {})[1])


def test_signed_mode_refuses_a_wrong_key_unknown_robot_or_altered_fields(tmp_path, monkeypatch):
    ctx = _signed_context(tmp_path, monkeypatch)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _assert_refused(_resolve(ctx, _CREATECA, _signed_body(key=other_key))[1])
    _assert_refused(_resolve(ctx, _CREATECA, _signed_body(did="999"))[1])
    altered = _signed_body()
    altered["uid"] = ["1"]
    _assert_refused(_resolve(ctx, _CREATECA, altered)[1])
    garbled = _signed_body()
    garbled["signature"] = ["not base64!"]
    _assert_refused(_resolve(ctx, _CREATECA, garbled)[1])
