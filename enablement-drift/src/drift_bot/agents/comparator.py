"""
Comparator Agent — compares expected UI state against captured state to find drift.

Input: DocExpectations + DocCapture
Output: DriftReport
"""

from difflib import SequenceMatcher
from ..models.expectations import DocExpectations, UILabel
from ..models.capture import DocCapture, CapturedLabel
from ..models.findings import DriftReport, DriftFinding
from ..config import KNOWN_RENAMES


def compare(expectations: DocExpectations, capture: DocCapture) -> DriftReport:
    """
    Compare expected state against captured state and produce a drift report.

    For each expected label at each step:
    1. Exact text match → no drift
    2. Case-insensitive match → no drift (minor formatting)
    3. Known rename match → label_rename with high confidence
    4. Fuzzy match (>0.8 similarity) → candidate drift
    5. No match → missing_element
    """
    findings: list[DriftFinding] = []
    total_labels = 0

    # Index captures by step_id for fast lookup
    capture_map = {c.step_id: c for c in capture.captures}

    for exp in expectations.expectations:
        captured = capture_map.get(exp.step_id)

        if not captured:
            # Step wasn't captured (navigation failed, etc.)
            for label in exp.labels:
                total_labels += 1
                findings.append(DriftFinding(
                    step_id=exp.step_id,
                    drift_class="ambiguous",
                    severity="medium",
                    confidence=0.5,
                    expected=label.text,
                    observed="(step not captured)",
                    reasoning=f"Navigation to step {exp.step_id} failed or was skipped",
                ))
            continue

        if captured.error:
            for label in exp.labels:
                total_labels += 1
                findings.append(DriftFinding(
                    step_id=exp.step_id,
                    drift_class="ambiguous",
                    severity="medium",
                    confidence=0.3,
                    expected=label.text,
                    observed=f"(capture error: {captured.error})",
                    reasoning=f"Capture failed: {captured.error}",
                ))
            continue

        # Compare each expected label against captured labels
        for label in exp.labels:
            total_labels += 1
            finding = _match_label(label, captured, exp.step_id)
            if finding:
                findings.append(finding)

    return DriftReport(
        doc_source=expectations.doc_source,
        run_id=capture.run_id,
        org_url=capture.org_url,
        total_expectations=len(expectations.expectations),
        total_labels_checked=total_labels,
        findings=findings,
    )


def _match_label(label: UILabel, captured, step_id: str) -> DriftFinding | None:
    """
    Try to match an expected label against captured labels.
    Returns a DriftFinding if drift is detected, None if matched.
    """
    expected_text = label.text.strip()
    captured_labels = captured.accessible_labels

    # 1. Exact match
    for cl in captured_labels:
        if cl.text.strip() == expected_text:
            return None  # Perfect match, no drift

    # 2. Case-insensitive match
    expected_lower = expected_text.lower()
    for cl in captured_labels:
        if cl.text.strip().lower() == expected_lower:
            return None  # Close enough, no drift

    # 3. Known rename lookup
    if expected_text in KNOWN_RENAMES:
        renamed_to = KNOWN_RENAMES[expected_text]
        for cl in captured_labels:
            if cl.text.strip().lower() == renamed_to.lower():
                return DriftFinding(
                    step_id=step_id,
                    drift_class="terminology_update",
                    severity="high",
                    confidence=0.95,
                    expected=expected_text,
                    observed=cl.text.strip(),
                    suggested_correction=f'Replace "{expected_text}" with "{cl.text.strip()}"',
                    auto_merge_eligible=True,
                    reasoning=f'Known product rename: "{expected_text}" → "{renamed_to}"',
                )

    # 4. Reverse rename lookup (captured has old name, doc has new)
    reverse_renames = {v: k for k, v in KNOWN_RENAMES.items()}
    if expected_text in reverse_renames:
        old_name = reverse_renames[expected_text]
        for cl in captured_labels:
            if cl.text.strip().lower() == old_name.lower():
                return DriftFinding(
                    step_id=step_id,
                    drift_class="terminology_update",
                    severity="medium",
                    confidence=0.90,
                    expected=expected_text,
                    observed=cl.text.strip(),
                    suggested_correction=f'The UI still shows "{cl.text.strip()}" but the doc uses the new name "{expected_text}"',
                    auto_merge_eligible=False,
                    reasoning=f'Doc uses new terminology "{expected_text}" but UI still shows old name "{cl.text.strip()}"',
                )

    # 5. Fuzzy match — find the closest captured label
    best_match: CapturedLabel | None = None
    best_ratio = 0.0
    for cl in captured_labels:
        ratio = SequenceMatcher(None, expected_lower, cl.text.strip().lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = cl

    if best_match and best_ratio >= 0.80:
        return DriftFinding(
            step_id=step_id,
            drift_class="label_rename",
            severity="medium",
            confidence=best_ratio,
            expected=expected_text,
            observed=best_match.text.strip(),
            suggested_correction=f'Replace "{expected_text}" with "{best_match.text.strip()}"',
            auto_merge_eligible=best_ratio >= 0.85,
            reasoning=f'Fuzzy match ({best_ratio:.0%} similarity): "{expected_text}" ≈ "{best_match.text.strip()}"',
        )

    if best_match and best_ratio >= 0.60:
        return DriftFinding(
            step_id=step_id,
            drift_class="ambiguous",
            severity="medium",
            confidence=best_ratio,
            expected=expected_text,
            observed=best_match.text.strip(),
            auto_merge_eligible=False,
            reasoning=f'Partial match ({best_ratio:.0%}): "{expected_text}" ≈ "{best_match.text.strip()}" — needs human review',
        )

    # 6. Check if the text appears anywhere in the page text
    if captured.page_text and expected_lower in captured.page_text.lower():
        return DriftFinding(
            step_id=step_id,
            drift_class="ambiguous",
            severity="low",
            confidence=0.6,
            expected=expected_text,
            observed="(found in page text but not as a discrete UI element)",
            auto_merge_eligible=False,
            reasoning=f'Text "{expected_text}" found in page body but not matched to a specific UI element',
        )

    # 7. Not found at all
    return DriftFinding(
        step_id=step_id,
        drift_class="missing_element",
        severity="high",
        confidence=0.85,
        expected=expected_text,
        observed="(not found)",
        auto_merge_eligible=False,
        reasoning=f'Expected label "{expected_text}" not found in any captured UI element or page text',
    )
