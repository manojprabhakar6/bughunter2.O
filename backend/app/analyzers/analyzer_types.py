from dataclasses import dataclass

from app.schemas import Finding, SimulationResult


class AnalyzerUnavailable(Exception):
    pass


@dataclass
class AnalysisResult:
    corrected_code: str
    findings: list[Finding]
    simulation: SimulationResult
