import boto3
from botocore.config import Config
from app.settings import settings


# Explicitly configure region and signature version
cfg = Config(signature_version="s3v4", region_name=settings.AWS_REGION)

# Ensure we always hit the regional endpoint (not s3.amazonaws.com)
s3 = boto3.client(
    "s3",
    region_name=settings.AWS_REGION,
    endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com",
    config=cfg,
)


def presign_upload(key: str, content_type: str, max_mb: int = 200, ttl: int = 3600):
    """
    Generate a presigned POST URL for uploading directly to S3.
    """
    fields = {"Content-Type": content_type}
    conditions = [["content-length-range", 0, max_mb * 1024 * 1024]]
    return s3.generate_presigned_post(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=ttl,
    )


def presign_download(key: str, ttl: int = 3600):
    """
    Generate a presigned URL for downloading an object from S3.
    """
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key},
        ExpiresIn=ttl,
    )
