"""Structured-output validation of chat completion responses."""

import json
import re
from functools import lru_cache

from backend.config import SchemaField


CHAT_COMPLETIONS_PATH = "chat/completions"


@lru_cache(maxsize=256)
def _compile_field_validator(code: str):
    indented = "\n".join(f"    {line}" for line in code.splitlines())
    source = f"def _validator(cls, v):\n{indented}\n"
    namespace: dict = {"re": re}
    exec(compile(source, "<field_validator>", "exec"), namespace)
    return namespace["_validator"]


def run_field_validator(name: str, code: str, value: object) -> str | None:
    try:
        validator = _compile_field_validator(code)
    except SyntaxError as exc:
        return f"Field '{name}' validator has a syntax error: {exc}"
    try:
        validator(None, value)
    except ValueError as exc:
        return f"Field '{name}': {exc}"
    except Exception as exc:
        return f"Field '{name}' validator raised {type(exc).__name__}: {exc}"
    return None


def _matches_json_schema_type(value: object, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    return True


def validate_against_schema(payload: object, fields: list[SchemaField]) -> list[str]:
    if not isinstance(payload, dict):
        return ["Response body is not a JSON object"]

    issues: list[str] = []
    fields_by_name = {field.name: field for field in fields}
    for name in fields_by_name:
        if name not in payload:
            issues.append(f"Missing field '{name}'")

    for name, value in payload.items():
        field = fields_by_name.get(name)
        if not field:
            continue
        if not _matches_json_schema_type(value, field.type):
            issues.append(f"Field '{name}' expected type '{field.type}'")
            continue
        if field.validator_code.strip():
            issue = run_field_validator(name, field.validator_code, value)
            if issue:
                issues.append(issue)

    return issues


def extract_message_content(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None
    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def extract_sse_content(raw: bytes) -> str | None:
    found = False
    parts: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            continue
        delta = choices[0].get("delta")
        delta_content = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(delta_content, str):
            parts.append(delta_content)
            found = True
    return "".join(parts) if found else None


def synthesize_chat_completion_body(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def force_non_streaming_body(body: bytes) -> bytes:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(payload, dict) and payload.get("stream"):
        payload = {**payload, "stream": False}
        payload.pop("stream_options", None)
        return json.dumps(payload).encode("utf-8")
    return body


def request_wants_structured_output(body: bytes | None) -> bool:
    if not body:
        return False
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    response_format = payload.get("response_format")
    if not isinstance(response_format, dict):
        return False
    return response_format.get("type") == "json_schema"


def compute_validation_issues(
    path: str,
    status_code: int | None,
    body: bytes | None,
    truncated: bool,
    fields: list[SchemaField],
    request_body: bytes | None,
) -> list[str]:
    if path != CHAT_COMPLETIONS_PATH or not fields or not body or truncated:
        return []
    if not request_wants_structured_output(request_body):
        return []
    if status_code is None or status_code >= 400:
        return []

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ["Response body is not valid JSON"]

    content = extract_message_content(payload)
    if content is None:
        return ["Response is missing choices[0].message.content"]

    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError:
        return ["choices[0].message.content is not valid JSON"]

    return validate_against_schema(parsed_content, fields)
