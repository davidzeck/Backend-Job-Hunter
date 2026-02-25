"""
S3 / MinIO object storage helpers.

All functions use aioboto3 for async I/O.  The same code works against:
  - MinIO in development  (endpoint_url=http://localhost:9000)
  - Real AWS S3           (endpoint_url=None, boto3 discovers region automatically)

Key design choices
──────────────────
• Presigned POST (not PUT) for uploads: the POST policy document lets S3 enforce
  Content-Type and Content-Length-Range before accepting the bytes — the client
  cannot bypass those constraints.
• Download URLs are presigned GETs with a short TTL. Files are never streamed
  through the API server, keeping bandwidth usage near zero.
• Each function opens and closes its own S3 client context. aioboto3 clients are
  lightweight; connection pooling lives in the underlying botocore HTTP session.
"""
import re
import aioboto3
from typing import Optional

from app.core.config import settings


# ── S3 key construction ─────────────────────────────────────────────────────

def build_s3_key(user_id: str, cv_id: str, filename: str) -> str:
    """
    Canonical S3 key:  cvs/{prefix4}/{user_id}/{cv_id}/{safe_filename}

    The 4-hex prefix derived from user_id shards objects across 65,536 virtual
    partitions, preventing the S3 hot-partition bottleneck at high PUT rates.
    """
    prefix4 = user_id.replace("-", "")[:4]
    safe_name = _sanitize_filename(filename)
    return f"cvs/{prefix4}/{user_id}/{cv_id}/{safe_name}"


def _sanitize_filename(name: str) -> str:
    """Keep only safe ASCII chars for S3 keys; collapse whitespace to underscore."""
    # Keep letters, digits, dash, dot, underscore
    safe = re.sub(r"[^\w\-.]", "_", name, flags=re.ASCII)
    # Collapse consecutive underscores
    safe = re.sub(r"_+", "_", safe)
    return safe[:200]  # S3 key parts are limited to 1024 bytes total


# ── Session factory ──────────────────────────────────────────────────────────

def _s3_client():
    """Return an async context-manager for an S3 client configured from settings."""
    session = aioboto3.Session()
    kwargs = dict(
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_aws_access_key_id,
        aws_secret_access_key=settings.s3_aws_secret_access_key,
    )
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    return session.client("s3", **kwargs)


# ── Upload: presigned POST ───────────────────────────────────────────────────

async def generate_presign_upload(s3_key: str, max_size_bytes: int) -> dict:
    """
    Generate an S3 presigned POST for direct client-to-S3 upload.

    Returns a dict with keys:
      url    — the S3 POST URL (action attribute of the HTML form)
      fields — dict of form fields that MUST be included before the 'file' field

    The embedded policy enforces:
      • Content-Type must be application/pdf
      • File size must be between 1 byte and max_size_bytes

    The presigned POST expires in settings.s3_presign_upload_expires seconds (900 by default).
    """
    async with _s3_client() as s3:
        response = await s3.generate_presigned_post(
            Bucket=settings.s3_bucket_name,
            Key=s3_key,
            Conditions=[
                {"Content-Type": "application/pdf"},
                ["content-length-range", 1, max_size_bytes],
            ],
            ExpiresIn=settings.s3_presign_upload_expires,
        )
    # response = {"url": "...", "fields": {"key": ..., "Content-Type": ..., ...}}
    return response


# ── Download: presigned GET ──────────────────────────────────────────────────

async def generate_presign_download(s3_key: str, filename: Optional[str] = None) -> str:
    """
    Generate a time-limited presigned GET URL for downloading a CV.

    If filename is provided, sets Content-Disposition so browsers download with
    that name instead of the raw S3 key.

    Expires in settings.s3_presign_download_expires seconds (3600 by default).
    """
    params: dict = {"Bucket": settings.s3_bucket_name, "Key": s3_key}
    if filename:
        params["ResponseContentDisposition"] = (
            f'attachment; filename="{_sanitize_filename(filename)}"'
        )

    async with _s3_client() as s3:
        url = await s3.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=settings.s3_presign_download_expires,
        )
    return url


# ── Object existence check ───────────────────────────────────────────────────

async def object_exists(s3_key: str) -> bool:
    """
    Return True if the object is present in the bucket.

    Uses head_object (zero-byte request) — much cheaper than get_object.
    Called in the confirm step to verify the client actually uploaded the file.
    """
    try:
        async with _s3_client() as s3:
            await s3.head_object(Bucket=settings.s3_bucket_name, Key=s3_key)
        return True
    except Exception:
        return False


# ── Download bytes (for Celery workers) ─────────────────────────────────────

async def download_bytes(s3_key: str) -> bytes:
    """
    Download the full object and return its bytes.

    Used by the Celery worker to read the PDF for text extraction.
    Workers run in separate containers and cannot reach the local filesystem.
    """
    async with _s3_client() as s3:
        response = await s3.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
        async with response["Body"] as stream:
            return await stream.read()


# ── Delete ───────────────────────────────────────────────────────────────────

async def delete_object(s3_key: str) -> None:
    """
    Delete a single object from S3.

    Called when a user deletes a CV or when the confirm step fails.
    Silently succeeds if the object doesn't exist (idempotent).
    """
    async with _s3_client() as s3:
        await s3.delete_object(Bucket=settings.s3_bucket_name, Key=s3_key)


async def delete_user_objects(user_id: str) -> int:
    """
    Delete ALL objects under cvs/{prefix4}/{user_id}/ for GDPR right-to-erasure.

    Returns the number of objects deleted.
    Uses batch delete (up to 1000 per call) to minimise request count.
    """
    prefix4 = user_id.replace("-", "")[:4]
    prefix = f"cvs/{prefix4}/{user_id}/"

    deleted = 0
    async with _s3_client() as s3:
        paginator = s3.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            keys = [{"Key": obj["Key"]} for obj in objects]
            await s3.delete_objects(
                Bucket=settings.s3_bucket_name,
                Delete={"Objects": keys, "Quiet": True},
            )
            deleted += len(keys)

    return deleted
