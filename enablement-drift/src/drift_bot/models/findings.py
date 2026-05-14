"""
Drift findings models — the comparison results.

Produced by the Comparator agent.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class DriftFinding(BaseModel):
    """One instance of detected drift."""
    step_id: str = Field(description="Which step this drift was found at")
    drift_class: Literal[
        "label_rename",
        "terminology_update",
        "broken_link",
        "procedural_change",
        "outcome_change",
        "missing_element",
        "ambiguous"
    ] = Field(description="Classification of the drift type")
    severity: Literal["high", "medium", "low"] = Field(default="medium")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the finding (0-1)")
    expected: str = Field(description="What the doc said should be there")
    observed: str = Field(default="", description="What was actually found (empty if missing)")
    evidence_screenshot_path: str | None = Field(default=None)
    suggested_correction: str | None = Field(default=None, description="Proposed doc update text")
    auto_merge_eligible: bool = Field(default=False, description="Safe for automatic doc update?")
    reasoning: str = Field(default="", description="Why this was classified this way")


class DriftReport(BaseModel):
    """Complete report from one comparison run."""
    doc_source: str
    run_id: str
    run_timestamp: datetime = Field(default_factory=datetime.utcnow)
    org_url: str = Field(default="")
    total_expectations: int = Field(default=0)
    total_labels_checked: int = Field(default=0)
    findings: list[DriftFinding] = Field(default_factory=list)

    @property
    def drift_count(self) -> int:
        return len(self.findings)

    @property
    def auto_merge_count(self) -> int:
        return sum(1 for f in self.findings if f.auto_merge_eligible)

    @property
    def needs_review_count(self) -> int:
        return sum(1 for f in self.findings if not f.auto_merge_eligible)

    def to_markdown(self) -> str:
        """Generate a human-readable summary report."""
        lines = [
            f"# Drift Detection Report",
            f"",
            f"**Document**: {self.doc_source}",
            f"**Org**: {self.org_url}",
            f"**Run**: {self.run_id}",
            f"**Time**: {self.run_timestamp.isoformat()}",
            f"",
            f"## Summary",
            f"",
            f"- **Steps checked**: {self.total_expectations}",
            f"- **Labels checked**: {self.total_labels_checked}",
            f"- **Drift found**: {self.drift_count}",
            f"- **Auto-mergeable**: {self.auto_merge_count}",
            f"- **Needs review**: {self.needs_review_count}",
            f"",
        ]

        if not self.findings:
            lines.append("No drift detected. Documentation is up to date.")
        else:
            lines.append("## Findings")
            lines.append("")
            for i, f in enumerate(self.findings, 1):
                emoji = "🔴" if f.severity == "high" else "🟡" if f.severity == "medium" else "🟢"
                lines.append(f"### {emoji} Finding {i}: {f.drift_class} (step {f.step_id})")
                lines.append(f"")
                lines.append(f"- **Severity**: {f.severity}")
                lines.append(f"- **Confidence**: {f.confidence:.0%}")
                lines.append(f"- **Expected**: {f.expected}")
                lines.append(f"- **Observed**: {f.observed or '(not found)'}")
                if f.suggested_correction:
                    lines.append(f"- **Suggested fix**: {f.suggested_correction}")
                lines.append(f"- **Auto-merge eligible**: {'Yes' if f.auto_merge_eligible else 'No'}")
                lines.append(f"- **Reasoning**: {f.reasoning}")
                lines.append(f"")

        return "\n".join(lines)
