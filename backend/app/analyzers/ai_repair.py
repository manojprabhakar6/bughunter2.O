"""Optional AI repair boundary. A provider must be explicitly configured.

The static analyzers remain the default; this module intentionally does not
claim semantic fixes without a configured, validated model provider.
"""
from dataclasses import dataclass
from typing import Protocol

from app.schemas import Finding, Language


@dataclass(frozen=True)
class RepairRequest:
    source: str
    language: Language
    program_goal: str
    findings: tuple[Finding, ...]


class RepairProvider(Protocol):
    def propose(self, request: RepairRequest) -> str | None: ...


def propose_ai_repair(
    request: RepairRequest, provider: RepairProvider | None = None
) -> str | None:
    """Return a provider proposal, or None when no AI provider is configured."""
    if provider is None:
        return None
    return provider.propose(request)
