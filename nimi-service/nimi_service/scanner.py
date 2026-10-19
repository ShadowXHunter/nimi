"""High-confidence sensitive-data detection.

This does NOT try to find every secret in free text (that's unreliable). It catches well-known formats with
few false positives, and is used to:
  - flag incoming messages before any model sees them (critical hits -> the request is held),
  - mask those formats in outgoing replies as a backstop (the skill is instructed never to write values).
Findings never contain the value itself — only its type and position.
"""
import re
from dataclasses import dataclass

# (type, severity, pattern). Severity: critical = held from models; high = flagged.
PATTERNS = [
    ("private-key", "critical", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----")),
    ("gcp-service-account-key", "critical", re.compile(r'"private_key"\s*:\s*"-----BEGIN')),
    ("aws-access-key", "critical", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-api-key", "critical", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("github-token", "critical", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}\b|\bgithub_pat_[0-9A-Za-z_]{40,}\b")),
    ("slack-token", "critical", re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}\b")),
    ("anthropic-or-openai-key", "critical", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{32,}\b")),
    ("chat-webhook-key", "critical",
     re.compile(r"chat\.googleapis\.com/v1/spaces/[^\s/?]+/messages\?[^\s]*\b(?:key|token)=[A-Za-z0-9_\-%]{16,}")),
    ("jwt", "high", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer-token", "high", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}")),
    ("url-credentials", "high", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]{3,}@[^\s/]+")),
    ("password-assignment", "high",
     re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)"
                r"\b\s*[:=]\s*['\"]?(?!<|\$\{|\{\{|\*{3}|x{3}|null\b|none\b|true\b|false\b)[^\s'\"<>]{6,}")),
]


@dataclass
class Finding:
    type: str
    severity: str
    line: int           # 1-based line number in the scanned text
    start: int
    end: int

    def describe(self) -> dict:
        """Safe to store/notify: no value."""
        return {"type": self.type, "severity": self.severity, "line": self.line}


def scan(text: str) -> list:
    findings = []
    for type_, severity, pattern in PATTERNS:
        for m in pattern.finditer(text or ""):
            findings.append(Finding(type_, severity, text.count("\n", 0, m.start()) + 1, m.start(), m.end()))
    return sorted(findings, key=lambda f: f.start)


def worst_severity(findings: list):
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return min((f.severity for f in findings), key=order.get) if findings else None


def mask(text: str) -> tuple:
    """Replace every finding in `text` with <redacted: type>. Returns (masked_text, findings)."""
    findings = scan(text)
    out, pos = [], 0
    for f in findings:
        if f.start < pos:            # overlapping match already masked
            continue
        out.append(text[pos:f.start])
        out.append(f"<redacted: {f.type}>")
        pos = f.end
    out.append(text[pos:])
    return "".join(out), findings
