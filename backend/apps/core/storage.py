from whitenoise.storage import CompressedManifestStaticFilesStorage


class NonStrictManifestStorage(CompressedManifestStaticFilesStorage):
    """
    WhiteNoise compressed+manifest storage with manifest_strict=False.

    Django's default ManifestStaticFilesStorage raises ValueError when a static
    file referenced in a template is not in the staticfiles.json manifest.
    With DEBUG=False this causes a 500 on any page that loads such a file.

    Setting manifest_strict=False falls back to the raw path instead of crashing.
    Grappelli and other third-party admin themes sometimes reference files that
    collectstatic does not include in the manifest, so this is the safe default.
    """

    manifest_strict = False
