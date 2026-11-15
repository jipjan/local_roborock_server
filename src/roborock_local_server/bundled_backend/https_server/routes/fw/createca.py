"""Local answer for the camera's ``POST .../fwapi/createca`` TURN request.

Background (issue #18): when the app requests a live camera preview, the robot
firmware first fetches a TURN/STUN configuration from a fwapi endpoint before it
starts the WebRTC session. The firmware builds the host as ``<input>[:15] +
"iot.roborock.com"``, where the input depends on the model: the Saros 10R (a144)
uses the onboarding ``country_domain`` and the Qrevo MaxV (a87) uses ``token.r``
including its explicit port. A short input with a trailing ``/`` resolves it to
this server (see docs/camera.md). Until this route existed the request fell
through to the catchall, the robot got no TURN config, and
``start_camera_preview`` returned ``-10012 / request turnserver failed`` with the
app showing an instant timeout.

This route answers with a TURN/STUN server operated alongside the stack (for
example coturn). The robot then completes the full WebRTC handshake against it and
a go2rtc ``roborock://`` consumer receives frames, all without reaching the
Roborock cloud. The response contract is reconstructed from a Saros 10R
(``roborock.vacuum.a144``) and also works on the Qrevo MaxV
(``roborock.vacuum.a87``); the firmware accepts a plain-JSON body:

    { "code": 200, "msg": "success",
      "data":   { url, user, pwd, ttl, urls, username, credential, realm },
      "result": { ...same object... } }

``data`` carries the legacy ``user``/``pwd`` field names and ``result`` the
ICE-style ``username``/``credential`` names; both are populated so either parser
in the firmware is satisfied. When ``[turn].mode`` is ``disabled`` (the default)
the request falls through to the catchall, so behaviour is unchanged for anyone not
running a TURN server.

With ``[turn].createca_auth = "signed"`` (the default) the credentials go only to a robot whose
request signature verifies with the public key this server recovered for it at onboarding; anyone
else gets ``code 401`` with the relay address and no credentials. ``"open"`` answers every request,
for firmware that does not sign it.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from shared.context import ServerContext

from ..bootstrap.catchall import build as _build_catchall

# The firmware posts to this path on the iot/fwapi host; accept common prefixes.
_MATCH_SUFFIX = "/fwapi/createca"


def match(path: str, _method: str = "POST") -> bool:
    return path.rstrip("/").endswith(_MATCH_SUFFIX)


def build_turn_payload(
    *, host: str, port: int, username: str, password: str, realm: str, ttl: int
) -> dict[str, Any]:
    """Build the TURN/STUN credential object the firmware expects."""
    turn_url = f"turn:{host}:{port}"
    stun_url = f"stun:{host}:{port}"
    return {
        "url": turn_url,
        "user": username,
        "pwd": password,
        "ttl": ttl,
        "urls": [turn_url, stun_url],
        "username": username,
        "credential": password,
        "realm": realm or host,
    }


LOG = logging.getLogger(__name__)


def signed_canonical(params: dict[str, list[str]]) -> tuple[str, str] | None:
    """Return ``(signed text, signature b64)`` for a form-encoded createca request, or None.

    The Saros 20 Neo (a296) posts ``did=..&pid=..&uid=..&signature=<b64>`` and signs everything
    before the signature, in order, with RSA PKCS#1 v1.5 / SHA-256 and the same key it uses for
    ``/region``. The parsed values are rejoined in request order; they are plain ids and model
    names, so this equals the raw form text.
    """
    signature = (params.get("signature") or [""])[0]
    if not signature or not (params.get("did") or [""])[0]:
        return None
    parts = [
        f"{key}={value}"
        for key, values in params.items()
        if key != "signature" and not key.startswith("__")
        for value in values
    ]
    return "&".join(parts), signature


def verify_request_signature(ctx: ServerContext, params: dict[str, list[str]]) -> str | None:
    """Return the request's ``did`` when its signature verifies with that robot's public key."""
    signed = signed_canonical(params)
    if signed is None:
        return None
    canonical, signature_b64 = signed
    did = params["did"][0]
    pubkey = ctx.device_public_key(did)
    if pubkey is None:
        return None
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        pubkey.verify(signature, canonical.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, binascii.Error, ValueError):
        return None
    return did


def build(
    ctx: ServerContext,
    query_params: dict[str, list[str]],
    body_params: dict[str, list[str]],
    clean_path: str,
) -> dict[str, Any]:
    if not getattr(ctx, "turn_enabled", False) or not getattr(ctx, "turn_host", ""):
        # No TURN server configured -> keep prior behaviour (camera unavailable).
        return _build_catchall(ctx, query_params, body_params, clean_path)
    if getattr(ctx, "turn_createca_auth", "signed") != "open":
        params = body_params if body_params.get("signature") else query_params
        if verify_request_signature(ctx, params) is None:
            LOG.warning(
                "createca refused: no valid robot signature (did=%s)",
                (params.get("did") or ["-"])[0],
            )
            # No credentials; the relay address lets onboarding preflight check the route.
            return {
                "code": 401,
                "msg": "robot signature required",
                "data": {"url": f"turn:{ctx.turn_host}:{ctx.turn_port}", "signed": True},
            }
    payload = build_turn_payload(
        host=ctx.turn_host,
        port=ctx.turn_port,
        username=ctx.turn_username,
        password=ctx.turn_password,
        realm=ctx.turn_realm,
        ttl=ctx.turn_ttl,
    )
    return {"code": 200, "msg": "success", "data": payload, "result": payload}
