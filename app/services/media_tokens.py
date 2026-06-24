"""
Short-lived signed-URL tokens for serving document PDFs to Twilio.

Twilio's media-send requires a public URL it can GET (server-to-server). We
don't want the PDF endpoint to be permanently public, so each send issues a
random 32-char token stored in Redis with a 15-minute TTL. The media
endpoint validates the token against the document_id before returning bytes.

Token lifecycle:
  - issue_token(doc_id)            ->  random token, stored in Redis (TTL 900s)
  - GET /documents/{id}/media/{tk} ->  validates and serves
  - 15 min later, token expires    ->  URL stops working

We deliberately don't single-use tokens: Twilio sometimes retries media
fetches, and strict single-use would race-condition with that. 15 minutes is
short enough that leaked URLs aren't a meaningful exposure window.
"""
import os
import secrets
import logging

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 900   # 15 minutes
TOKEN_KEY_PREFIX = "media_token:"

_redis_client = None


def _get_redis():
    """Lazy redis client. REDIS_URL is set by Railway's Redis plugin."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
        except ImportError as e:
            raise RuntimeError("redis package not installed - cannot issue media tokens") from e
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            raise RuntimeError("REDIS_URL not set - cannot issue media tokens")
        _redis_client = redis.from_url(redis_url, decode_responses=True)
    return _redis_client


def issue_token(document_id: str) -> str:
    """Generate a token bound to a document_id, valid for TOKEN_TTL_SECONDS."""
    token = secrets.token_urlsafe(32)
    _get_redis().setex(f"{TOKEN_KEY_PREFIX}{token}", TOKEN_TTL_SECONDS, document_id)
    logger.info(f"Issued media token for doc {document_id[:8]} (TTL {TOKEN_TTL_SECONDS}s)")
    return token


def validate_token(token: str) -> str | None:
    """Return the document_id this token is bound to, or None if invalid/expired."""
    if not token:
        return None
    try:
        return _get_redis().get(f"{TOKEN_KEY_PREFIX}{token}")
    except Exception as e:
        logger.error(f"Redis lookup failed for token: {e}")
        return None
