import io
import os
import shutil
from uuid import uuid4

from PIL import Image
from django.utils.deconstruct import deconstructible
from rest_framework.exceptions import ValidationError

# Allowed MIME types for uploads
ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB


@deconstructible
class FilePath(object):
    def __init__(self, sub_path, custom_name=None):
        self.path = sub_path
        self.custom_name = custom_name

    def __call__(self, instance, filename):
        ext = filename.split('.')[-1].lower()
        filename = '{}.{}'.format(self.custom_name if self.custom_name else uuid4().hex, ext)
        return os.path.join(self.path.format(**instance.__dict__), filename)


def file_path(folder, custom_name=''):
    return FilePath(os.path.join(folder, custom_name))


def delete_file(path):
    if os.path.exists(path):
        os.remove(path)


def test_file(name='test.png', extension='png'):
    file = io.BytesIO()
    image = Image.new('RGBA', size=(100, 100), color=(155, 0, 0))
    image.save(file, extension)
    file.name = name
    file.seek(0)
    return file


def clear_uploads():
    uploads = './uploads'
    if os.path.exists(uploads) and os.path.isdir(uploads):
        shutil.rmtree(uploads)


def validate_file_size(value):
    """Validate that uploaded file does not exceed 5 MB."""
    if value.size > MAX_UPLOAD_SIZE:
        raise ValidationError(f'Maximum file size is {MAX_UPLOAD_SIZE // (1024 * 1024)} MB.')
    return value


def validate_image_type(value):
    """Validate that uploaded file has an allowed image MIME type."""
    content_type = getattr(value, 'content_type', '')
    if content_type and content_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError(
            f'Unsupported file type: {content_type}. '
            f'Allowed: {", ".join(ALLOWED_IMAGE_TYPES)}'
        )
    return value


def get_file_extension_os(url):
    _, file_extension = os.path.splitext(url)
    return file_extension
