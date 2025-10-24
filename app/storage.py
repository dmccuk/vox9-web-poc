# app/storage.py

import boto3
from botocore.config import Config
from app.settings import settings

cfg = Config(signature_version="s3v4", region_name=settings.AWS_REGION)

s3 = boto3.client(
    "s3",
    region_name=settings.AWS_REGION,
    endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com",
    config=cfg,
)

def presign_upload(key: str, content_type: str, max_mb: int = 200, ttl: int = 3600):
    max_bytes = max_mb * 1024 * 1024

    fields = {
        "Content-Type": content_type,
        "key": key,
    }

    conditions = [
        ["content-length-range", 0, max_bytes],
        ["starts-with", "$key", ""],                # allow any key (or use your prefix)
        ["starts-with", "$Content-Type", ""],       # 👈 allow Content-Type field
        # Optional hardening (uncomment if you like):
        # {"bucket": settings.S3_BUCKET},
    ]

    return s3.generate_presigned_post(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=ttl,
    )

def presign_download(key: str, ttl: int = 3600):
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key},
        ExpiresIn=ttl,
    )
