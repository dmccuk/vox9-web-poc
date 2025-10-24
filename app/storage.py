import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from datetime import datetime
from typing import List, Tuple, Optional, Dict

from app.settings import settings

# Force v4 signing + regional endpoint to avoid redirects/CORS issues
cfg = Config(signature_version="s3v4", region_name=settings.AWS_REGION)
s3 = boto3.client(
    "s3",
    region_name=settings.AWS_REGION,
    endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com",
    config=cfg,
)

def presign_upload(key: str, content_type: str, max_mb: int = 200, ttl: int = 3600):
    """
    Generate a presigned POST for browser-based direct upload.
    Includes Content-Type condition to avoid 'extra input fields' error.
    """
    max_bytes = max_mb * 1024 * 1024
    fields = {
        "Content-Type": content_type,
        "key": key,
    }
    conditions = [
        ["content-length-range", 0, max_bytes],
        ["starts-with", "$key", ""],              # or f"{settings.S3_INPUT_PREFIX}"
        ["starts-with", "$Content-Type", ""],     # allow any content type
    ]
    return s3.generate_presigned_post(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=ttl,
    )

def presign_download(
    key: str,
    ttl: int = 3600,
    *,
    as_attachment: bool = False,
    download_name: Optional[str] = None,
):
    """
    Generate a presigned GET URL for downloading/streaming an object.
    If as_attachment=True, add Content-Disposition: attachment to force download.
    """
    params = {"Bucket": settings.S3_BUCKET, "Key": key}
    if as_attachment:
        if not download_name:
            download_name = key.split("/")[-1] or "download"
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params=params,
        ExpiresIn=ttl,
    )

def list_objects(prefix: str, continuation_token: Optional[str] = None, max_keys: int = 100) -> Tuple[List[Dict], Optional[str]]:
    """
    List S3 objects under a prefix with pagination.
    Requires IAM permission: s3:ListBucket on the bucket (optionally scoped to the prefixes).
    """
    kwargs = {
        "Bucket": settings.S3_BUCKET,
        "Prefix": prefix,
        "MaxKeys": max_keys,
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token

    try:
        resp = s3.list_objects_v2(**kwargs)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        msg = e.response.get("Error", {}).get("Message")
        raise RuntimeError(f"S3 List error: {code} {msg}")

    items: List[Dict] = []
    for obj in resp.get("Contents", []) or []:
        # Skip folder placeholders if any
        if obj["Key"].endswith("/") and obj.get("Size", 0) == 0:
            continue
        lm = obj.get("LastModified")
        items.append({
            "key": obj["Key"],
            "size": obj.get("Size", 0),
            "last_modified": lm.isoformat() if hasattr(lm, "isoformat") else str(lm),
        })

    next_token = resp.get("NextContinuationToken")
    return items, next_token
