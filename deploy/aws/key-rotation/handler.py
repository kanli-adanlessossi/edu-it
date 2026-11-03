"""Key rotation Lambda for the production Bedrock credential (ai-content task 4.14).

Invoked by an EventBridge schedule every 6 hours. It generates a new short-term Bedrock API key
with AWS's token generator, using the Lambda's own IAM role, and writes it as a new version of
the Secrets Manager secret named by ``LLM_PROVIDER_API_KEY_SECRET_ID``. The worker reads that
secret (``SecretsManagerCredentialSource``) and re-reads it once after an HTTP 401 or 403.

Secrecy. The key is held in one local variable and passed to ``put_secret_value``. Logs and
exceptions carry only its fingerprint, which uses the same scheme as
``edu_it_infrastructure.llm.credentials.credential_fingerprint`` (a test keeps the two equal).

Failure. ``put_secret_value`` adds a version and moves ``AWSCURRENT`` only on success, so a failed
write leaves the previous version current. The handler then raises :class:`RotationError`, which
makes the invocation count as a Lambda error and trips the CloudWatch alarm.

This module is deployed on its own and must not import the application packages.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from datetime import timedelta
from typing import Any

__all__ = ["KEY_LIFETIME", "RotationError", "fingerprint", "handler", "rotate"]

_log = logging.getLogger(__name__)
_log.setLevel(logging.INFO)

SECRET_ID_ENV = "LLM_PROVIDER_API_KEY_SECRET_ID"
REGION_ENV = "AWS_REGION"
# Requested lifetime. The key is valid for the shorter of this and the role credentials' expiry.
# The schedule (6 hours) stays inside it.
KEY_LIFETIME = timedelta(hours=12)

TokenGenerator = Callable[[str], str]


class RotationError(Exception):
    """Rotation failed. The message never holds a key."""


def fingerprint(api_key: str) -> str:
    """Non-reversible identifier of ``api_key`` (same scheme as the application)."""
    return "key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _generate_token(region: str) -> str:
    """Generate a short-term Bedrock API key from the Lambda role's credentials."""
    from aws_bedrock_token_generator import provide_token  # bundled with the function

    token: str = provide_token(region=region, expiry=KEY_LIFETIME)
    return token


def rotate(
    *,
    secret_id: str,
    region: str,
    secrets_client: Any,
    generate_token: TokenGenerator = _generate_token,
) -> str:
    """Write a new key version and return its fingerprint."""
    if not secret_id:
        raise RotationError(f"{SECRET_ID_ENV} is not set")
    if not region:
        raise RotationError(f"{REGION_ENV} is not set")

    try:
        key = generate_token(region)
    except Exception as err:  # never echo err args: they could carry credential material
        raise RotationError(f"cannot generate a key: {type(err).__name__}") from None
    if not isinstance(key, str) or not key.strip():
        raise RotationError("the token generator returned no key")

    key = key.strip()
    print_id = fingerprint(key)
    try:
        secrets_client.put_secret_value(SecretId=secret_id, SecretString=key)
    except Exception as err:
        raise RotationError(
            f"cannot write secret {secret_id!r}, previous version kept "
            f"(new key {print_id}): {type(err).__name__}"
        ) from None

    _log.info("rotated the live provider key: %s", print_id)
    return print_id


def handler(event: Any, context: Any) -> dict[str, str]:
    """Lambda entry point. ``event`` and ``context`` are unused."""
    import boto3  # provided by the Lambda runtime

    print_id = rotate(
        secret_id=os.environ.get(SECRET_ID_ENV, ""),
        region=os.environ.get(REGION_ENV, ""),
        secrets_client=boto3.client("secretsmanager"),
    )
    return {"fingerprint": print_id}
