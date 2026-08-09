from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    request_id = getattr(request, "request_id", None)
    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    machine_code = getattr(exc, "machine_code", None)
    if not machine_code and hasattr(exc, "get_codes"):
        codes = exc.get_codes()
        machine_code = codes if isinstance(codes, str) else "validation_error"
    machine_code = machine_code or "api_error"
    original = response.data
    response.data = {
        "error": {
            "code": machine_code,
            "message": str(detail or "Request could not be completed."),
            "details": original if detail is None else None,
        },
        "code": machine_code,
        "detail": detail or "Request could not be completed.",
    }
    if request_id:
        response.data["request_id"] = request_id
    return response
