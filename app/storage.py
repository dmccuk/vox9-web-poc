import boto3
from botocore.config import Config
from datetime import datetime
from typing import List, Tuple, Optional, Dict
from app.settings import settings

# Force v4 + regional endpoint to avoid redirects/CORS issues
cfg = Config(signature_version="s3v4", region_name=settings.AWS_REGION)
s3 = boto3.client(
    "s3",
    region_name=settings.AWS_REGION,
    endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com",
    config=cfg,
)

def presign_upload(key: str, content_type: str, max_mb: int = 200, ttl: int = 3600):
    max_bytes = max_mb * 1024 * 1024
    fields = { "Content-Type": content_type, "key": key }
    conditions = [
        ["content-length-range", 0, max_bytes],
        ["starts-with", "$key", ""],
        ["starts-with", "$Content-Type", ""],
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

def list_objects(prefix: str, continuation_token: Optional[str] = None, max_keys: int = 100) -> Tuple[List[Dict], Optional[str]]:
    kwargs = {
        "Bucket": settings.S3_BUCKET,
        "Prefix": prefix,
        "MaxKeys": max_keys,
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token

    resp = s3.list_objects_v2(**kwargs)
    items: List[Dict] = []
    for obj in resp.get("Contents", []):
        # Skip folder placeholders
        if obj["Key"].endswith("/") and obj["Size"] == 0:
            continue
        items.append({
            "key": obj["Key"],
            "size": obj["Size"],
            "last_modified": obj["LastModified"].isoformat() if isinstance(obj["LastModified"], datetime) else str(obj["LastModified"]),
        })
    next_token = resp.get("NextContinuationToken")
    return items, next_token
