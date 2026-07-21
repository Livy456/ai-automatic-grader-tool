from __future__ import annotations

import uuid
from typing import BinaryIO, Optional

import boto3
from botocore.client import Config as BotoClientConfig
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig

from ..config import Config

# Multipart: good default for notebooks, videos, large PDFs
_TRANSFER = TransferConfig(
    multipart_threshold=8 * 1024 * 1024,
    multipart_chunksize=8 * 1024 * 1024,
    max_concurrency=10,
    use_threads=True,
)

# UPDATE THIS LATER TO USE S3 STORAGE INSTEAD OF MINIO; MINIO integrates with AWS S3

def _addressing_style(cfg: Config) -> str:
    """
    Get the addressing style for the MinIO client.

    Parameters:
        cfg: The configuration for the MinIO client.
    """
    raw = (cfg.MINIO_ADDRESSING_STYLE or "").strip().lower()
    if raw in ("path", "virtual"):
        return raw
    return "path"


def minio_client(cfg: Config):
    """
    Low-level boto3 client for MinIO object storage.

    Parameters:
        cfg: The configuration for the MinIO client.
    """
    kwargs: dict = {
        "region_name": cfg.OBJECT_STORAGE_REGION or cfg.MINIO_REGION or "us-east-1",
        "config": BotoClientConfig(
            signature_version="s3v4",
            s3={"addressing_style": _addressing_style(cfg)},
        ),
    }
    if cfg.MINIO_ACCESS_KEY:
        kwargs["aws_access_key_id"] = cfg.MINIO_ACCESS_KEY
    if cfg.MINIO_SECRET_KEY:
        kwargs["aws_secret_access_key"] = cfg.MINIO_SECRET_KEY
    if cfg.MINIO_ENDPOINT:
        kwargs["endpoint_url"] = cfg.MINIO_ENDPOINT
    use_ssl = cfg.MINIO_SECURE
    kwargs["use_ssl"] = use_ssl
    return boto3.client("s3", **kwargs)

def minio_client_for_presign(cfg: Config):
    """
    Boto client used only to build presigned URLs returned to browsers.

    The URL's host must be reachable from the user's machine (not a Docker-only hostname like
    ``minio``). Server-side object-store calls continue to use :func:`minio_client`.

    Parameters:
        cfg: The configuration for the MinIO client.
    """
    endpoint = (cfg.MINIO_PRESIGN_ENDPOINT or cfg.MINIO_ENDPOINT or "").strip()
    
    kwargs: dict = {
        "region_name": cfg.OBJECT_STORAGE_REGION or cfg.MINIO_REGION or "us-east-1",
        "config": BotoClientConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path" if endpoint else _addressing_style(cfg)},
        ),
    }
    
    if cfg.MINIO_ACCESS_KEY:
        kwargs["aws_access_key_id"] = cfg.MINIO_ACCESS_KEY
    if cfg.MINIO_SECRET_KEY:
        kwargs["aws_secret_access_key"] = cfg.MINIO_SECRET_KEY
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if endpoint:
        kwargs["use_ssl"] = endpoint.lower().startswith("https://")
    else:
        kwargs["use_ssl"] = cfg.MINIO_SECURE
    return boto3.client("s3", **kwargs)

def _upload_fileobj(
    cfg: Config,
    fileobj: BinaryIO,
    key: str,
    content_type: Optional[str],
    extra_args: Optional[dict] = None,
) -> str:
    """
    Upload a file object to MinIO.

    Parameters:
        cfg: The configuration for the MinIO client.
        fileobj: The file object to upload.
        key: The key to upload the file to.
        content_type: The content type of the file.
        extra_args: Extra arguments to pass to the upload.
    """

    client = minio_client(cfg)
    extra = dict(extra_args or {})
    if content_type:
        extra["ContentType"] = content_type
    upload_kw = {"Config": _TRANSFER}
    if extra:
        upload_kw["ExtraArgs"] = extra
    client.upload_fileobj(fileobj, cfg.MINIO_BUCKET, key, **upload_kw)
    return key

def upload_from_fastapi_file(cfg: Config, upload_file, key: str) -> str:
    """
    Stream upload from a FastAPI/Starlette ``UploadFile`` into MinIO without loading the whole
    file into RAM. Starlette already buffers the multipart body into ``upload_file.file`` (a
    ``SpooledTemporaryFile`` that spills to disk once it exceeds Starlette's in-memory
    threshold) while parsing the request, so by the time a route handler runs, reading it here
    is a plain, already-bounded-memory file read — no extra spooling needed on our side.

    Parameters:
        cfg: The configuration for the MinIO client.
        upload_file: The upload file to upload.
        key: The key to upload the file to.
    """
    content_type = upload_file.content_type or "application/octet-stream"
    upload_file.file.seek(0)
    return _upload_fileobj(cfg, upload_file.file, key, content_type)

def put_object(
    cfg: Config,
    data_stream: BinaryIO,
    length: int,
    content_type: str,
    prefix: str,
) -> str:
    """
    Upload from a bounded stream (e.g. BytesIO). Key: {prefix}/{uuid_hex}.
    Prefer upload_from_fastapi_file for arbitrary large uploads.

    Parameters:
        cfg: The configuration for the MinIO client.
        data_stream: The data stream to upload.
        length: The length of the data stream.
        content_type: The content type of the data stream.
        prefix: The prefix to upload the data stream to.
    """
    key = f"{prefix}/{uuid.uuid4().hex}"
    if length <= cfg.MINIO_INLINE_UPLOAD_MAX_BYTES:
        body = data_stream.read(length)
        minio_client(cfg).put_object(
            Bucket=cfg.MINIO_BUCKET,
            Key=key,
            Body=body,
            ContentType=content_type or "application/octet-stream",
        )
        return key
    return _upload_fileobj(cfg, data_stream, key, content_type)

def get_object_bytes(cfg: Config, key: str) -> bytes:
    """
    Download full object (used by Celery grading).

    Parameters:
        cfg: The configuration for the MinIO client.
        key: The key to download the object from.
    """
    r = minio_client(cfg).get_object(Bucket=cfg.MINIO_BUCKET, Key=key)
    return r["Body"].read()

def get_presigned_url(
    cfg: Config,
    key: str,
    method: str = "GET",
    expires: int = 3600,
    *,
    bucket: str | None = None,
) -> str:
    """
    Presign GET/PUT. Use ``bucket`` when the object lives in a non-default reports bucket.

    Parameters:
        cfg: The configuration for the MinIO client.
        key: The key to presign the object from.
        method: The method to presign the object from.
        expires: The expiration time for the presigned URL.
        bucket: The bucket to presign the object from.
    """

    b = (bucket or "").strip() or cfg.MINIO_BUCKET
    client = minio_client_for_presign(cfg)
    m = method.upper()
    if m == "GET":
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": b, "Key": key},
            ExpiresIn=expires,
        )
    if m == "PUT":
        return client.generate_presigned_url(
            "put_object",
            Params={"Bucket": b, "Key": key},
            ExpiresIn=expires,
        )
    raise ValueError(f"unsupported presign method: {method}")

def presigned_put_url(
    cfg: Config,
    key: str,
    content_type: str,
    expires: Optional[int] = None,
) -> str:
    """
    Browser → MinIO direct upload. Client must send the same Content-Type header on PUT.
    Keeps large files off the API host (production ingress is metadata + presign only).
    
    Parameters:
        cfg: The configuration for the MinIO client.
        key: The key to presign the object from.
        content_type: The content type of the object.
        expires: The expiration time for the presigned URL.
    """
    exp = expires if expires is not None else cfg.MINIO_PRESIGN_PUT_EXPIRES
    client = minio_client_for_presign(cfg)
    ct = content_type or "application/octet-stream"
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": cfg.MINIO_BUCKET,
            "Key": key,
            "ContentType": ct,
        },
        ExpiresIn=exp,
        HttpMethod="PUT",
    )

def object_exists(cfg: Config, key: str) -> bool:
    """
    Return True if object is present (used to finalize direct uploads).

    Parameters:
        cfg: The configuration for the MinIO client.
        key: The key to check if the object exists.
    """
    try:
        minio_client(cfg).head_object(Bucket=cfg.MINIO_BUCKET, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound", "404 Not Found"):
            return False
        raise