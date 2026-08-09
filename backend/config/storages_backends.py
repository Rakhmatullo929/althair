from storages.backends.s3boto3 import S3Boto3Storage


class PrivateMediaStorage(S3Boto3Storage):
    """
    • All uploads go to <bucket>/media/…
    • Objects are always *private*
    • .url() is *always* presigned (query-string auth)
    """
    location            = "media"      # optional – neat key prefix
    default_acl         = "private"    # never “public-read”
    querystring_auth    = True         # force presign in .url()
    querystring_expire  = 3600         # secs; change if you wish
    file_overwrite      = False        # keep originals
    # (optional) SIGv4 is now default, but be explicit:
    signature_version   = "s3v4"
