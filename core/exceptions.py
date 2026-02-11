"""
Custom exception handler following HackSoft Django Styleguide (Approach 1).

Converts Django's ValidationError into DRF's ValidationError so the API
always returns a consistent JSON error format.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions
from rest_framework.serializers import as_serializer_error
from rest_framework.views import exception_handler


def custom_exception_handler(exc, ctx):
    """
    1. Convert Django ValidationError -> DRF ValidationError.
    2. Ensure ``response.data`` always has the ``detail`` key.
    """
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(as_serializer_error(exc))

    response = exception_handler(exc, ctx)

    if response is None:
        return response

    if isinstance(exc.detail, (list, dict)):
        response.data = {"detail": response.data}

    return response
