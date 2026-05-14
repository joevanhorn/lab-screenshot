"""
Captured state models — what Playwright actually found in the live UI.

Produced by the Navigator agent.
"""

from datetime import datetime
from pydantic import BaseModel, Field


class CapturedLabel(BaseModel):
    """A UI element found on the live page."""
    text: str = Field(description="Visible text content of the element")
    selector: str = Field(default="", description="CSS selector that matched this element")
    tag: str = Field(default="", description="HTML tag (button, a, span, etc.)")
    role: str = Field(default="", description="ARIA role or semantic role")
    bounding_box: dict | None = Field(default=None, description="Position on page {x, y, width, height}")
    attributes: dict = Field(default_factory=dict, description="Relevant attributes (data-se, aria-label, href, etc.)")


class CapturedState(BaseModel):
    """State captured at one navigation step."""
    step_id: str = Field(description="Matches the step_id from expectations")
    url: str = Field(default="", description="Current page URL")
    page_title: str = Field(default="", description="Current page title")
    screenshot_path: str = Field(default="", description="Path to screenshot file")
    screenshot_base64: str = Field(default="", description="Base64 encoded screenshot for LLM vision")
    accessible_labels: list[CapturedLabel] = Field(default_factory=list, description="All visible UI labels found")
    page_text: str = Field(default="", description="Full visible text on the page (truncated)")
    navigation_breadcrumb: list[str] = Field(default_factory=list, description="How we navigated to get here")
    capture_timestamp: datetime = Field(default_factory=datetime.utcnow)
    error: str | None = Field(default=None, description="Error message if capture failed at this step")


class DocCapture(BaseModel):
    """Complete capture for one document's expectations."""
    doc_source: str = Field(description="Source document path/ID")
    run_id: str = Field(description="Unique run identifier")
    org_url: str = Field(description="Okta org URL that was captured")
    captures: list[CapturedState] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = Field(default=None)
