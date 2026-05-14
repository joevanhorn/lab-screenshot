"""
Interpreter Agent — reads a markdown lab guide and extracts structured expectations.

Input: markdown file path
Output: DocExpectations (serializable to expectations.yaml)
"""

import json
from pathlib import Path
from datetime import datetime

from ..models.expectations import DocExpectations, Expectation, UILabel
from ..config import LLM_MODEL, LLM_API_KEY, LLM_API_BASE

EXTRACTION_PROMPT = """You are analyzing an Okta admin console lab guide. Your job is to extract structured expectations about what the UI should look like at each step.

For each numbered step in the guide, identify:
1. A unique step_id (e.g., "step-1", "step-2a")
2. A description of what the user is instructed to do
3. Navigation breadcrumb: the path through the admin console to reach this step (e.g., ["Security", "Authentication Policies"])
4. URL hint: if the doc mentions a specific URL or path
5. Expected UI labels: specific text that should be visible on the page — button labels, tab names, section headers, field labels, menu items. Only include labels the doc EXPLICITLY claims will be visible.
6. The source text: the exact passage from the doc this step comes from

Return ONLY valid JSON (no markdown fences, no preamble) in this exact format:
{
  "expectations": [
    {
      "step_id": "step-1",
      "description": "Navigate to the Authentication Policies page",
      "navigation": ["Security", "Authentication Policies"],
      "url_hint": "/admin/access/authentication",
      "labels": [
        {"text": "Authentication Policies", "locator_hint": "section header", "semantic_role": "section_header"},
        {"text": "Add a policy", "locator_hint": "button", "semantic_role": "button"}
      ],
      "source_text": "Navigate to Security > Authentication Policies. You should see the Authentication Policies page with an 'Add a policy' button."
    }
  ]
}

Focus on labels that are specific and verifiable — named buttons, specific tab labels, exact section headers. Skip generic text like "click here" or "the page loads".

Here is the lab guide to analyze:

---
{doc_text}
---

Return the JSON now:"""


def interpret(guide_path: str, model: str | None = None) -> DocExpectations:
    """
    Read a markdown lab guide and extract structured expectations via LLM.

    Args:
        guide_path: Path to the markdown file
        model: Optional LLM model override

    Returns:
        DocExpectations with extracted steps and labels
    """
    path = Path(guide_path)
    if not path.exists():
        raise FileNotFoundError(f"Guide not found: {guide_path}")

    doc_text = path.read_text()
    model = model or LLM_MODEL

    # Call LLM
    prompt = EXTRACTION_PROMPT.format(doc_text=doc_text)
    raw_response = _call_llm(prompt, model)

    # Strip markdown fences if present
    content = raw_response.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    # Parse JSON
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\nResponse: {content[:500]}")

    # Build DocExpectations
    expectations = []
    for item in data.get("expectations", []):
        labels = [
            UILabel(
                text=lbl.get("text", ""),
                locator_hint=lbl.get("locator_hint", ""),
                semantic_role=lbl.get("semantic_role", "other"),
            )
            for lbl in item.get("labels", [])
        ]
        expectations.append(Expectation(
            step_id=item.get("step_id", ""),
            description=item.get("description", ""),
            navigation=item.get("navigation", []),
            url_hint=item.get("url_hint", ""),
            labels=labels,
            source_text=item.get("source_text", ""),
        ))

    return DocExpectations(
        doc_title=path.stem,
        doc_source=str(path),
        extraction_timestamp=datetime.utcnow(),
        expectations=expectations,
    )


def _call_llm(prompt: str, model: str) -> str:
    """Call the LLM via litellm (supports Anthropic, Bedrock, proxy)."""
    import litellm

    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8192,
        "temperature": 0.2,
    }

    if LLM_API_KEY:
        kwargs["api_key"] = LLM_API_KEY
    if LLM_API_BASE:
        kwargs["api_base"] = LLM_API_BASE

    response = litellm.completion(**kwargs)
    return response.choices[0].message.content
