#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DEBUG", "true")

import django  # noqa: E402

django.setup()

from ai_runtime.providers import FakeAIProvider, _detect_language  # noqa: E402
from ai_runtime.tools import TOOL_REGISTRY  # noqa: E402


def main():
    cases = [
        json.loads(line)
        for line in (Path(__file__).parent / "cases.jsonl").read_text().splitlines()
        if line.strip()
    ]
    failures = []
    for case in cases:
        result = FakeAIProvider().generate(
            prompt="deterministic booking evaluation",
            tools=[spec.provider_schema() for spec in TOOL_REGISTRY.values()],
            latest_message=case["input"],
            max_output_tokens=300,
        )
        tool = result.tool_calls[0].name if result.tool_calls else None
        checks = {
            "language": _detect_language(case["input"]) == case["language"],
            "tool": tool == case["expected_tool"],
            "authorized": tool in TOOL_REGISTRY,
            "no_false_confirmation": "confirmed" not in result.text.casefold(),
            "tenant_safe": "another tenant data" not in result.text.casefold(),
        }
        failed = [key for key, passed in checks.items() if not passed]
        if failed:
            failures.append({"id": case["id"], "failed": failed, "tool": tool})
    print(json.dumps({"suite": "booking", "cases": len(cases), "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
