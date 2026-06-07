"""AWS S3 (and S3-compatible) credential management."""
from __future__ import annotations

import json
from typing import Dict, Optional

from .config import CONFIG_DIR

_CREDS_FILE = CONFIG_DIR / "s3_credentials.json"

# Pre-configured endpoints for popular S3-compatible services.
# Each entry: display_label → {endpoint_url, region, notes}
S3_PRESETS: Dict[str, Dict[str, str]] = {
    "aws": {
        "label": "Amazon S3",
        "endpoint_url": "",
        "region": "us-east-1",
        "notes": "Standard AWS S3. Choose the region closest to you.",
    },
    "backblaze": {
        "label": "Backblaze B2",
        "endpoint_url": "https://s3.us-west-004.backblazeb2.com",
        "region": "us-west-004",
        "notes": (
            "Replace '004' with your B2 bucket's region code. "
            "Find it in the Backblaze console under Buckets → Endpoint."
        ),
    },
    "cloudflare": {
        "label": "Cloudflare R2",
        "endpoint_url": "https://<ACCOUNT_ID>.r2.cloudflarestorage.com",
        "region": "auto",
        "notes": (
            "Replace <ACCOUNT_ID> with your Cloudflare account ID "
            "(found in the R2 dashboard URL)."
        ),
    },
}


class S3Auth:
    """Stores and validates AWS credentials for S3 access.

    Credentials are persisted as JSON in ``~/.config/cloudsync/s3_credentials.json``.
    No OAuth flow is needed — the user supplies an access key + secret directly.
    """

    def __init__(self) -> None:
        self.access_key: str = ""
        self.secret_key: str = ""
        self.region: str = "us-east-1"
        self.endpoint_url: Optional[str] = None  # for S3-compatible services
        self._load()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @property
    def is_authenticated(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def save(
        self,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ) -> None:
        """Persist credentials to disk."""
        self.access_key = access_key.strip()
        self.secret_key = secret_key.strip()
        self.region = region.strip() or "us-east-1"
        self.endpoint_url = endpoint_url.strip() if endpoint_url else None
        self._persist()

    def validate(
        self,
        access_key: str,
        secret_key: str,
        region: str,
        endpoint_url: Optional[str] = None,
        bucket_target: Optional[str] = None,
    ) -> str:
        """Verify credentials and optionally confirm access to a bucket.

        When *bucket_target* is given, performs an ``s3:ListBucket`` call
        against that specific bucket/prefix.  When omitted, uses STS
        ``get_caller_identity`` — this requires only valid credentials and no
        S3 permissions, so it works even if the IAM policy only grants access
        to specific buckets.

        Returns the access key (used as the display email).
        """
        import boto3
        import botocore.exceptions

        creds: dict = dict(
            aws_access_key_id=access_key.strip(),
            aws_secret_access_key=secret_key.strip(),
            region_name=region.strip() or "us-east-1",
        )
        if endpoint_url:
            creds["endpoint_url"] = endpoint_url.strip()

        try:
            if bucket_target:
                s3 = boto3.client("s3", **creds)
                bucket, prefix = _split_bucket_prefix(bucket_target)
                if not bucket:
                    raise ValueError(
                        "Bucket is required (example: my-bucket or my-bucket/folder)."
                    )
                list_kwargs: dict = {"Bucket": bucket, "MaxKeys": 1}
                if prefix:
                    list_kwargs["Prefix"] = f"{prefix.rstrip('/')}/"
                s3.list_objects_v2(**list_kwargs)
            else:
                # Use STS so we don't need s3:ListAllMyBuckets.
                # S3-compatible services (B2, R2) may not support STS; fall back
                # to a harmless S3 call if STS is unavailable.
                sts_creds = {k: v for k, v in creds.items() if k != "endpoint_url"}
                try:
                    sts = boto3.client("sts", **sts_creds)
                    sts.get_caller_identity()
                except Exception:
                    # Non-AWS endpoint — just verify the S3 client can be created
                    # (credentials are validated on first actual bucket operation).
                    boto3.client("s3", **creds)

            return access_key.strip()
        except botocore.exceptions.ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "AccessDenied" and bucket_target:
                bucket, prefix = _split_bucket_prefix(bucket_target)
                scope = f"bucket '{bucket}'"
                if prefix:
                    scope = f"bucket '{bucket}' and prefix '{prefix}/'"
                raise ValueError(
                    "Access denied while listing objects in "
                    f"{scope}. Grant at least: s3:ListBucket on the bucket "
                    "and s3:GetObject/s3:PutObject/s3:DeleteObject on objects."
                ) from exc

            raise ValueError(
                f"AWS error ({code}): {exc.response['Error']['Message']}"
            ) from exc
        except botocore.exceptions.NoCredentialsError as exc:
            raise ValueError("No credentials provided.") from exc
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    def sign_out(self) -> None:
        self.access_key = ""
        self.secret_key = ""
        self.region = "us-east-1"
        self.endpoint_url = None
        if _CREDS_FILE.exists():
            _CREDS_FILE.unlink()

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if not _CREDS_FILE.exists():
            return
        try:
            data = json.loads(_CREDS_FILE.read_text())
            self.access_key = data.get("access_key", "")
            self.secret_key = data.get("secret_key", "")
            self.region = data.get("region", "us-east-1")
            self.endpoint_url = data.get("endpoint_url") or None
        except Exception:
            pass

    def _persist(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "region": self.region,
        }
        if self.endpoint_url:
            data["endpoint_url"] = self.endpoint_url
        _CREDS_FILE.write_text(json.dumps(data, indent=2))
        _CREDS_FILE.chmod(0o600)


def _split_bucket_prefix(target: str) -> tuple[str, str]:
    """Split ``bucket`` or ``bucket/prefix`` into bucket + prefix."""
    text = target.strip().lstrip("/")
    parts = text.split("/", 1)
    bucket = parts[0].strip()
    prefix = parts[1].strip() if len(parts) > 1 else ""
    return bucket, prefix


def example_bucket_policy(bucket_target: str) -> str:
    """Return an example bucket policy for a bucket or bucket/prefix target."""
    bucket, prefix = _split_bucket_prefix(bucket_target)
    if not bucket:
        raise ValueError("Bucket is required (example: my-bucket/photos).")

    statements = [
        {
            "Sid": "AllowAccountListBucket",
            "Effect": "Allow",
            "Principal": {"AWS": "arn:aws:iam::<YOUR_ACCOUNT_ID>:root"},
            "Action": "s3:ListBucket",
            "Resource": f"arn:aws:s3:::{bucket}",
        }
    ]

    object_resource = f"arn:aws:s3:::{bucket}/*"
    if prefix:
        object_resource = f"arn:aws:s3:::{bucket}/{prefix.rstrip('/')}/*"
        statements[0]["Condition"] = {
            "StringLike": {
                "s3:prefix": [
                    prefix,
                    f"{prefix.rstrip('/')}/*",
                ]
            }
        }

    statements.append(
        {
            "Sid": "AllowAccountObjectAccess",
            "Effect": "Allow",
            "Principal": {"AWS": "arn:aws:iam::<YOUR_ACCOUNT_ID>:root"},
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
            ],
            "Resource": object_resource,
        }
    )

    policy = {
        "Version": "2012-10-17",
        "Statement": statements,
    }
    return json.dumps(policy, indent=2)
