import boto3
from .settings import settings

s3 = boto3.client("s3", region_name=settings.AWS_REGION)


def presign_upload(key: str, content_type: str, max_mb: int = 200, ttl: int = 3600):
    fields = {"Content-Type": content_type}
    conditions = [["content-length-range", 0, max_mb * 1024 * 1024]]
    return s3.generate_presigned_post(
        Bucket=settings.S3_BUCKET, Key=key, Fields=fields, Conditions=conditions, ExpiresIn=ttl
    )


def presign_download(key: str, ttl: int = 3600):
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key},
        ExpiresIn=ttl,
    )
