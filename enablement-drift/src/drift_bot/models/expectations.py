"""
Expected state models — what the documentation claims the UI should look like.

Produced by the Interpreter agent from a markdown lab guide.
Human-reviewable and editable as YAML.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class UILabel(BaseModel):
    """A specific UI label the documentation expects to be visible."""
    text: str = Field(description="The exact text the doc says should appear")
    locator_hint: str = Field(default="", description="CSS selector hint or description of where to find it")
    semantic_role: Literal[
        "button", "tab", "field_label", "section_header",
        "menu_item", "link", "badge", "toggle", "other"
    ] = Field(default="other", description="What kind of UI element this is")


class Expectation(BaseModel):
    """One step in the lab guide with expected UI state."""
    step_id: str = Field(description="Unique identifier for this step (e.g., 'step-3a')")
    description: str = Field(description="What this step instructs the user to do")
    navigation: list[str] = Field(default_factory=list, description="Breadcrumb of how to reach this UI state (e.g., ['Security', 'Authentication Policies'])")
    url_hint: str = Field(default="", description="Expected URL pattern or path")
    labels: list[UILabel] = Field(default_factory=list, description="UI labels expected to be visible at this step")
    source_text: str = Field(default="", description="The original doc text this was extracted from (for annotation back-reference)")


class DocExpectations(BaseModel):
    """Complete set of expectations extracted from one document."""
    doc_title: str = Field(description="Title of the source document")
    doc_source: str = Field(description="Path or ID of the source document")
    extraction_timestamp: datetime = Field(default_factory=datetime.utcnow)
    expectations: list[Expectation] = Field(default_factory=list)

    def to_yaml(self) -> str:
        """Serialize to YAML for human review."""
        import yaml
        return yaml.dump(self.model_dump(mode="json"), default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "DocExpectations":
        """Load from YAML."""
        import yaml
        data = yaml.safe_load(yaml_str)
        return cls.model_validate(data)
