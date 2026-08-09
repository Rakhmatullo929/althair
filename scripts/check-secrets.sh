#!/bin/sh
set -eu

if command -v gitleaks >/dev/null 2>&1; then
  exec gitleaks detect --no-banner --redact --source .
fi

python3 - <<'PY'
from pathlib import Path
import re
import sys

ROOT = Path.cwd()
SKIP_DIRS = {
    ".git", ".venv", "node_modules", ".next", ".turbo", "staticfiles",
    "media", "playwright-report", "test-results", "__pycache__",
}
SKIP_FILES = {Path("scripts/check-secrets.sh")}
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sh", ".env",
}
TOKEN_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\b"),
}
SENSITIVE_KEY = (
    r"(?:[A-Za-z0-9]+[_-])*"
    r"(?:api[_-]?key|secret|password|auth[_-]?token|webhook[_-]?secret|private[_-]?key)"
)
QUOTED_ASSIGNMENT = re.compile(
    rf"(?i)\b({SENSITIVE_KEY})\b\s*[:=]\s*([\"'])([^\"']{{16,}})\2"
)
BARE_CONFIG_ASSIGNMENT = re.compile(
    rf"(?i)^\s*({SENSITIVE_KEY})\s*[:=]\s*([^\s#]{{16,}})\s*$"
)
PLACEHOLDER_MARKERS = (
    "replace-", "change-me", "development-only", "local-development", "example",
    "placeholder", "os.environ", "getenv", "${", "<", "test-only",
)

findings = []
for path in ROOT.rglob("*"):
    relative = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in relative.parts) or relative in SKIP_FILES:
        continue
    if path.is_dir():
        continue
    lowered = path.name.lower()
    if lowered in {".env", "id_rsa", "id_ed25519"} or lowered.endswith((".pem", ".p12", ".pfx")):
        findings.append(("forbidden-secret-file", relative, 0))
        continue
    if path.suffix.lower() in {".zip", ".7z", ".rar", ".tgz"}:
        findings.append(("archive", relative, 0))
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
        "Dockerfile", ".gitignore", ".dockerignore",
    }:
        continue
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for number, line in enumerate(content.splitlines(), 1):
        for category, pattern in TOKEN_PATTERNS.items():
            if pattern.search(line):
                findings.append((category, relative, number))
        matches = list(QUOTED_ASSIGNMENT.finditer(line))
        if path.suffix.lower() in {".env", ".yaml", ".yml", ".ini", ".cfg", ".conf"}:
            bare_match = BARE_CONFIG_ASSIGNMENT.search(line)
            if bare_match:
                matches.append(bare_match)
        for match in matches:
            value = match.group(3 if match.re is QUOTED_ASSIGNMENT else 2).lower()
            if not any(marker in value for marker in PLACEHOLDER_MARKERS):
                findings.append(("credential-assignment", relative, number))

if findings:
    print(f"Secret scan failed with {len(findings)} redacted finding(s).", file=sys.stderr)
    for category, path, line in findings:
        location = f"{path}:{line}" if line else str(path)
        print(f"- {category}: {location} (value redacted)", file=sys.stderr)
    sys.exit(1)

print("Secret scan passed (fallback pattern scanner; values are never printed).")
print("Limitations: pattern scanning cannot prove absence of novel or encoded credentials.")
PY
