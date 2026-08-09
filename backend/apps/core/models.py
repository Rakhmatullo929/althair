from django.db import models
from django.db.models import SET_NULL
from django.utils.functional import cached_property
import uuid


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False, null=True)
    updated_at = models.DateTimeField(auto_now=True, editable=False, null=True)
    created_by = models.ForeignKey('users.User', SET_NULL, null=True, blank=True,
                                   related_name='created_%(model_name)ss')
    updated_by = models.ForeignKey('users.User', SET_NULL, null=True, blank=True,
                                   related_name='updated_%(model_name)ss')

    class Meta:
        abstract = True
        # ordering = ('id',)


class BinaryUUIDField(models.BinaryField):
    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 16  # Fixed length for UUID
        super().__init__(*args, **kwargs)

    def get_db_prep_value(self, value, connection, prepared=False):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value.bytes
        try:
            return uuid.UUID(value).bytes
        except ValueError:
            return value

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        # Convert memory view to bytes if necessary
        if isinstance(value, memoryview):
            value = value.tobytes()
        return uuid.UUID(bytes=value)

    def to_python(self, value):
        if value is None or isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(bytes=value)
        except TypeError:
            # Assuming value is a string representation of UUID
            return uuid.UUID(value)

    @cached_property
    def validators(self):
        return super().validators
