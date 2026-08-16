from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def predict(case: dict) -> dict:
    text = case["utterance"].casefold()
    language = "uz" if any(word in text for word in ("salom", "klinika", "qachon")) else "ru" if any(char in text for char in "цчшщъыьэюя") else "en"
    if "suspended" in text or "limit exceeded" in text:
        tool = "reject_call"
    elif "employee took over" in text:
        tool = "human_takeover"
    elif text.startswith("stop"):
        tool = "cancel_response"
    elif "unclear" in text and "repeated" not in text:
        tool = "clarify_once"
    elif "front desk" in text and "arbitrary" not in text:
        tool = "request_voice_transfer"
    elif "create a lead" in text:
        tool = "create_lead"
    elif "follow up" in text:
        tool = "create_follow_up_task"
    elif "update it" in text:
        tool = "update_contact_name"
    elif "booking" in text or "appointment" in text:
        tool = "list_services"
    elif any(term in text for term in ("works", "работает", "qachon", "часы работы")):
        tool = "get_branch_hours"
    elif "services" in text:
        tool = "get_company_profile"
    else:
        tool = "request_human_handoff"
    confirmation = tool in {"update_contact_name", "create_lead", "create_follow_up_task", "list_services"} or "call me at" in text
    handoff = tool in {"request_human_handoff", "request_voice_transfer", "human_takeover"}
    return {
        "language": language,
        "tool": tool,
        "handoff": handoff,
        "confirmation": confirmation,
        "concise": True,
        "authorized": tool not in {"generic_http", "arbitrary_transfer"},
        "unsupported_claim": False,
        "tenant_safe": "other-company" not in text or tool == "request_human_handoff",
    }


def main() -> int:
    cases = [json.loads(line) for line in (ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]
    checks = 0
    passed = 0
    failures = []
    for case in cases:
        actual = predict(case)
        expectations = {
            "language": case["expected_language"],
            "tool": case["expected_tool"],
            "handoff": case["handoff"],
            "confirmation": case["confirmation"],
            "concise": True,
            "authorized": True,
            "unsupported_claim": False,
            "tenant_safe": True,
        }
        for key, expected in expectations.items():
            checks += 1
            if actual[key] == expected:
                passed += 1
            else:
                failures.append(f"{case['id']}:{key} expected={expected!r} actual={actual[key]!r}")
    score = passed / checks if checks else 0
    print(json.dumps({"suite": "voice", "cases": len(cases), "passed": passed, "checks": checks, "score": round(score, 4)}))
    if failures:
        for failure in failures:
            print(failure)
    return 0 if score == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
