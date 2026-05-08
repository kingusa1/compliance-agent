"""S3-compatible storage backend (works against AWS S3, MinIO, Cloudflare R2).

Uses boto3 with optional `endpoint_url` so the same code drives local
MinIO during dev and AWS in prod. Signed URLs use boto3's pre-signer.
"""
from __future__ import annotations

import boto3
from botocore.config import Config

from app.config import settings
from app.storage import StorageBackend


class S3Backend(StorageBackend):
    def __init__(self) -> None:
        client_kwargs = {
            "region_name": settings.s3_region,
            "aws_access_key_id": settings.s3_access_key or None,
            "aws_secret_access_key": settings.s3_secret_key or None,
            "config": Config(signature_version="s3v4"),
        }
        if settings.s3_endpoint:
            client_kwargs["endpoint_url"] = settings.s3_endpoint
        self._s3 = boto3.client("s3", **{k: v for k, v in client_kwargs.items() if v is not None})
        self._bucket = settings.s3_bucket

    def upload_blob(self, local_path: str, remote_key: str, content_type: str = "application/octet-stream") -> str:
        self._s3.upload_file(
            Filename=local_path,
            Bucket=self._bucket,
            Key=remote_key,
            ExtraArgs={"ContentType": content_type},
        )
        return remote_key

    def download_blob(self, remote_key: str, local_path: str) -> str:
        self._s3.download_file(Bucket=self._bucket, Key=remote_key, Filename=local_path)
        return local_path

    def signed_url(self, remote_key: str, expires_in: int = 3600) -> str:
        return self._s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self._bucket, "Key": remote_key},
            ExpiresIn=expires_in,
        )

    def delete_blob(self, remote_key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=remote_key)
