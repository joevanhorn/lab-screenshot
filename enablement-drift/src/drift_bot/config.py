"""
Configuration and environment variable loading.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# LLM
LLM_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
LLM_API_BASE = os.getenv("LITELLM_API_BASE", "")

# Okta
OKTA_ORG_URL = os.getenv("OKTA_ORG_URL", "")
OKTA_USERNAME = os.getenv("OKTA_USERNAME", "")
OKTA_PASSWORD = os.getenv("OKTA_PASSWORD", "")
OKTA_TOTP_SECRET = os.getenv("OKTA_TOTP_SECRET", "")

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"

# Known product renames (for boosting confidence on terminology_update drift)
KNOWN_RENAMES = {
    "Governance Engine": "Entitlement Engine",
    "Access Certifications": "Access Certification Reviews",
}
