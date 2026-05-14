# Enablement-as-Code Drift Bot — Build Plan

This is the master build plan for the documentation drift detection bot described in `documentation-drift-detection-proposal.html`. It is structured to be handed to Claude Code one phase at a time. Each phase contains a goal, the work to be done, the tests required, the done-criteria, what is deferred, and a Claude Code task brief you can paste directly.

---

## What this plan delivers

A **2-minute demo video** showing the drift detection and auto-repair loop running against one real Google Doc, with one or two seeded drift instances. The system underneath the video is real — agents actually run, state actually captures, the doc actually updates. The video is the deliverable; the system is what makes the video credible.

This plan deliberately does not build:

- A production-grade orchestrator
- A scheduled or event-driven pipeline
- Multi-doc validation at scale
- Anything beyond what the video needs to land

Anything beyond the video is **explicitly out of scope** for the hackathon and called out in each phase.

---

## Architecture recap

Six agents in a deterministic pipeline. See the proposal for the full architecture diagram and agent responsibilities. The build order is informed by data flow:

```
Phase 0  →  Phase 1  →  Phase 2  →  Phase 3  →  Phase 4  →  Phase 5  →  Phase 6  →  Phase 7  →  Phase 8
Setup       Models +    Interpreter Navigator   Comparator  Annotator   Reviewer +  Orchestrate Video
+ reuse     demo doc    (LLM read   (Playwright (text +     (Google     notify     (CLI)       production
audit                   of doc)     capture)    vision LLM) Docs API)
```

Phases 2 and 3 are independent and can be parallelized if a second engineer joins. Phases 5 and 6 are also parallelizable after Phase 4. Solo build order is strict serial.

---

## Conventions and stack

| Choice | Selection | Rationale |
|---|---|---|
| Language | Python 3.11+ | Matches existing bot, mature library ecosystem |
| Dep management | `uv` | Fast, reliable, modern; falls back to pip if needed |
| Browser automation | Playwright (Python) | Matches existing bot |
| LLM | Anthropic API directly | No framework overhead; agents are just functions |
| Data models | Pydantic v2 | Schema enforcement at every agent boundary |
| CLI | Typer | Clean for multi-command pipelines |
| Testing | pytest | Standard; supports TDD workflow |
| Lint/format | ruff | Single tool, fast |
| YAML | PyYAML | For `expectations.yaml` and policy config |
| Google APIs | `google-api-python-client` + `google-auth` | Official, well-documented |
| Slack | Incoming webhook | 5-minute setup; no app registration |
| Secrets | `.env` via `python-dotenv` | Hackathon-grade; never committed |
| State storage | Local filesystem under `runs/<run_id>/` | No cloud dependencies |

---

## Repository layout

```
enablement-drift/
├── README.md
├── BUILD_PLAN.md                  # this file
├── REUSE.md                       # produced in Phase 0
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── src/
│   └── drift_bot/
│       ├── __init__.py
│       ├── cli.py                 # Typer entry point
│       ├── models/
│       │   ├── __init__.py
│       │   ├── expectations.py    # DocExpectations, Expectation, UILabel
│       │   ├── capture.py         # DocCapture, CapturedState, CapturedLabel
│       │   └── findings.py        # DriftReport, DriftFinding
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── interpreter.py     # LLM doc reading → expectations.yaml
│       │   ├── navigator.py       # Playwright + state capture
│       │   ├── comparator.py      # text match + vision LLM
│       │   ├── annotator.py       # Google Docs write-back
│       │   └── reviewer.py        # policy engine + Slack
│       ├── integrations/
│       │   ├── __init__.py
│       │   ├── google_docs.py     # auth + Docs API wrappers
│       │   ├── google_drive.py    # auth + Drive API wrappers
│       │   ├── anthropic_client.py
│       │   └── slack.py           # webhook poster
│       └── config.py              # env vars, policy loading
│
├── tests/
│   ├── conftest.py                # fixtures (mock LLM responses, etc.)
│   ├── unit/
│   │   ├── test_interpreter.py
│   │   ├── test_navigator.py
│   │   ├── test_comparator.py
│   │   ├── test_annotator.py
│   │   └── test_reviewer.py
│   └── integration/
│       └── test_end_to_end.py     # full pipeline against fixture doc
│
├── policy/
│   └── default.yaml               # drift class → action mapping
│
├── docs/                          # per-doc onboarding artifacts
│   └── governance-engine-lab/
│       ├── expectations.yaml      # human-reviewed output of Interpreter
│       └── doc-config.yaml        # doc-id, tenant prereqs, etc.
│
└── runs/                          # gitignored; one dir per run
    └── <run_id>/
        ├── captured_state.json
        ├── drift_findings.json
        ├── summary_report.md
        └── screenshots/
```

---

## Testing strategy (read this before Phase 1)

Per the coding rules, TDD is the default: write a failing test, watch it fail, make it pass. **However**, LLM-dependent agents (Interpreter, Comparator's vision step) create a real tension with the rule against testing mocked behavior. Be honest about how this gets handled.

| Agent | Logic to test directly | LLM call handling |
|---|---|---|
| Interpreter | YAML serialization, schema validation of LLM output, file I/O | Smoke test against real API once per phase, not per commit |
| Navigator | Locator resolution, state-capture schema, screenshot path generation | No LLM in this agent |
| Comparator | Text-match logic (deterministic), drift classification, confidence scoring | Vision LLM smoke test once per phase |
| Annotator | Google Docs API request construction, comment text generation | No LLM in this agent |
| Reviewer | Policy lookup logic, Slack message formatting | No LLM in this agent |

**Rule**: never write a test that asserts on a mocked LLM's response content. Test the *transformation logic* around the response (parsing, validation, schema enforcement). Test the LLM integration itself with one or two real API smoke tests per agent, not per unit.

Integration tests (`tests/integration/test_end_to_end.py`) run the full pipeline against a frozen fixture doc and assert on the structured outputs (`drift_findings.json`). These do hit the real LLM. Expect them to be slow and flaky in adversarial ways — gate them behind a `pytest -m integration` mark.

---

## Phase 0 — Foundations and existing-bot audit

**Goal**: Repo exists, dependencies install, Google Workspace access verified, and we have a written understanding of what's reusable from `okta-terraform-demo-template`.

### Work

- [ ] Create the new repo `enablement-drift` (private, GitHub)
- [ ] Initialize Python project with `uv init`, set Python 3.11+
- [ ] Add initial dependencies: `playwright`, `anthropic`, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `pydantic`, `typer`, `pyyaml`, `python-dotenv`, `pytest`, `ruff`
- [ ] Install Playwright browsers and Linux system dependencies in one step: `uv run playwright install --with-deps chromium` (the `--with-deps` flag is a no-op on macOS but installs required system libraries on Ubuntu/Debian via apt; requires sudo on Linux)
- [ ] Create directory layout per the spec above (empty `__init__.py` files in each package)
- [ ] `.gitignore` for Python, `.env`, `runs/`, `*.pem`, `*.json` credentials files
- [ ] Create `.env.example` with placeholders for `ANTHROPIC_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_KEY_PATH`, `SLACK_WEBHOOK_URL`, `OKTA_ORG_URL`, `OKTA_DEMO_USER_EMAIL`, `OKTA_DEMO_USER_PASSWORD`
- [ ] Provision a Google Cloud service account, download key JSON, store outside repo
- [ ] Enable Google Docs API and Google Drive API on the GCP project
- [ ] Verify service account auth works with a 5-line script that lists files visible to it
- [ ] **Audit the existing bot**: fetch and read `app.py` from `joevanhorn/okta-terraform-demo-template`, identify functions/classes that capture page state and DOM, identify navigation logic, identify what's deployment-coupled vs cleanly reusable. Produce `REUSE.md` at the repo root.

### Tests

No agent tests in this phase. Add one smoke test (`tests/unit/test_setup.py`) that asserts:

- All deps import without error
- Anthropic API key is set (skip if not)
- Google service account file path is set (skip if not)

### Done-criteria

- `uv run pytest tests/` passes (smoke tests only)
- `uv run python -c "from playwright.sync_api import sync_playwright"` succeeds
- A Google Doc shared with the service account can be read via the Docs API (validated by a manual script run)
- `REUSE.md` exists at the repo root with clear sections on: (a) reusable navigation/capture functions, (b) what's tightly coupled, (c) recommended import strategy

### Deferred

- CI setup
- Pre-commit hooks (add in Phase 7 if there's time)
- Documentation beyond README stub

### Claude Code task brief for Phase 0

> Set up a new Python project for the enablement-drift bot at the path I provide. Use `uv` for dependency management with Python 3.11+. Install the dependencies listed in BUILD_PLAN.md Phase 0. Install Playwright Chromium with system dependencies: `uv run playwright install --with-deps chromium`. The `--with-deps` flag is required on Ubuntu/Debian hosts (where Joe runs Claude Code on EC2) to install the system libraries Chromium needs; it's a no-op on macOS. The command requires sudo on Linux. Create the directory layout in BUILD_PLAN.md exactly, including empty `__init__.py` files. Create `.gitignore`, `.env.example`, and a stub README.md. Initialize git with an initial commit.
>
> Then perform the existing-bot audit: fetch the file `app.py` from `https://github.com/joevanhorn/okta-terraform-demo-template` (use the raw GitHub URL) and any related navigation module you find referenced. Read the code carefully. Produce `REUSE.md` at the repo root with the following sections: (1) Summary of what the existing bot does, (2) Functions/classes that capture page state and DOM in a structured way — with file paths and line ranges, (3) Functions that perform navigation based on instructions — with file paths and line ranges, (4) Code that is tightly coupled to deployment logic and should not be imported, (5) Recommended strategy: lift as a Python package, copy specific functions, or treat as inspiration only — with rationale. Do not yet write any agent code. Do not yet attempt to import or copy any code from the existing bot.

---

## Phase 1 — Data models and demo doc

**Goal**: All Pydantic models exist with passing schema tests; a real Google Doc exists in the demo workspace with seeded drift; the service account can read and write to it.

### Work

- [ ] Implement `models/expectations.py`:
  - `UILabel(text: str, locator_hint: str, semantic_role: Literal["button", "tab", "field_label", "section_header", "menu_item", "other"])`
  - `Expectation(step_id: str, description: str, navigation: list[str], labels: list[UILabel])`
  - `DocExpectations(doc_id: str, doc_title: str, doc_revision_id: str, expectations: list[Expectation])`
- [ ] Implement `models/capture.py`:
  - `CapturedLabel(text: str, selector: str, bounding_box: dict | None)`
  - `CapturedState(step_id: str, screenshot_path: str, dom_snippet: str, accessible_labels: list[CapturedLabel], navigation_breadcrumb: list[str], capture_timestamp: datetime)`
  - `DocCapture(doc_id: str, run_id: str, captures: list[CapturedState])`
- [ ] Implement `models/findings.py`:
  - `DriftFinding(step_id: str, drift_class: Literal["label_rename", "terminology_update", "broken_link", "procedural_change", "outcome_change", "ambiguous"], severity: Literal["high", "medium", "low"], confidence: float, expected: str, observed: str, evidence_screenshot_path: str | None, suggested_correction: str | None, auto_merge_eligible: bool, reasoning: str)`
  - `DriftReport(doc_id: str, run_id: str, run_timestamp: datetime, findings: list[DriftFinding])`
- [ ] Create the demo Google Doc by hand. Use a real-style OIG lab guide structure. Seed exactly two drift instances:
  - **Drift A (label rename)**: Doc says "click **Governance Engine** on the General tab" but the live admin console reads "Entitlement Engine". This is the headline drift.
  - **Drift B (procedural change candidate)**: Pick a step where the doc claims "no additional confirmation is required" but Okta now shows a confirmation dialog — OR — pick a terminology drift (a feature rename), depending on what's reproducible in the sandbox tenant. **Decide during Phase 1 which is more demo-able.**
- [ ] Share the doc with the service account email with **Editor** permission
- [ ] Create `docs/governance-engine-lab/doc-config.yaml` with the doc ID, the tenant URL, the admin user credentials reference, and any setup prerequisites (e.g., "OIG enabled, demo application exists, application is named 'Demo App'")

### Tests

- `tests/unit/test_models.py` — assert that each Pydantic model accepts valid inputs, rejects invalid ones, round-trips through JSON, and round-trips through YAML for `DocExpectations`. Write these tests **first**, then implement the models to make them pass.

### Done-criteria

- All model tests pass
- A Python REPL session can authenticate as the service account and read the demo doc's text via `google-api-python-client`
- The same session can write a test comment to the doc and delete it
- `doc-config.yaml` exists and is loadable via PyYAML

### Deferred

- Multi-doc configuration
- Doc revision tracking automation (Drive revision IDs are captured by hand for now)
- Polished demo doc styling — make it look like a real lab guide but don't over-design

### Claude Code task brief for Phase 1

> Read BUILD_PLAN.md Phase 1 carefully. Implement the three Pydantic v2 model files exactly as specified, in `src/drift_bot/models/`. Write the tests in `tests/unit/test_models.py` BEFORE implementing the models — confirm the tests fail, then implement to make them pass. Use `pytest` with the project's `uv` environment. Tests must cover: valid construction, invalid construction (missing required fields, wrong literal values), JSON round-trip, and YAML round-trip for DocExpectations.
>
> Do NOT create the demo Google Doc — Joe will do that by hand. Do create `docs/governance-engine-lab/doc-config.yaml` as a template with placeholder values and clear comments showing what each field is for. Do create a small standalone script `scripts/verify_doc_access.py` that loads the doc config, authenticates as the service account using the path in `.env`, fetches the doc by ID, prints the first 500 characters of the doc text, posts a test comment "drift-bot access verification — please ignore", and immediately deletes that comment. This script is the proof-of-access for Phase 1 sign-off.

---

## Phase 2 — Interpreter agent

**Goal**: Given a Google Doc, the Interpreter produces a structured `expectations.yaml` listing each step's expected UI labels and navigation context. Output is human-reviewable and human-editable.

### Work

- [ ] Implement `integrations/google_docs.py` — wrapper for `documents.get` and text extraction that preserves step-structure cues (headings, numbered lists)
- [ ] Implement `integrations/anthropic_client.py` — thin wrapper exposing a `call(prompt: str, model: str, max_tokens: int) -> str` function with retries on rate limits
- [ ] Implement `agents/interpreter.py`:
  - `interpret(doc_id: str) -> DocExpectations`
  - Internally: fetch doc text, build a structured prompt that asks Claude to extract `(step_id, description, navigation, labels)` tuples, parse JSON response into `DocExpectations`, validate with Pydantic, return
- [ ] Design the extraction prompt deliberately. The prompt must:
  - Explain the schema
  - Show one or two examples of input doc text → expected JSON output
  - Constrain output to JSON only (no preamble, no markdown fences)
  - Instruct the LLM to only extract labels the doc explicitly claims will be visible
- [ ] CLI command: `drift-bot interpret <doc-id> --out <path>` writes `expectations.yaml`
- [ ] Run against the demo doc, write `docs/governance-engine-lab/expectations.yaml`, **review and hand-edit** until it accurately reflects the doc's claims
- [ ] Commit the hand-reviewed `expectations.yaml`

### Tests

- `tests/unit/test_interpreter.py`:
  - Test JSON-string-to-DocExpectations parsing with fixture LLM responses (this tests our parsing, not the LLM)
  - Test handling of malformed JSON (should raise, not silently produce empty)
  - Test handling of partial extraction (some steps missing labels)
- One integration smoke test, marked `@pytest.mark.integration`, that actually calls the LLM on a small fixture doc text snippet and asserts the output validates as `DocExpectations`

### Done-criteria

- `drift-bot interpret <demo-doc-id>` produces a `DocExpectations` that parses without errors
- The hand-reviewed `expectations.yaml` contains both seeded drift instances as expectations (e.g., `Governance Engine` label is expected at the relevant step)
- The file is small enough that it's reasonable for a human to review in one sitting (< 200 lines for the demo doc)

### Deferred

- Doc revision change detection (re-running Interpreter only when doc changes)
- Multi-doc batch interpretation
- Prompt versioning

### Claude Code task brief for Phase 2

> Read BUILD_PLAN.md Phase 2. Implement `integrations/google_docs.py` first with a single function `fetch_doc_text(doc_id: str) -> str` that returns the doc body as plain text with step-cues preserved (e.g., insert markers for headings and list items). Use the service account auth from Phase 1.
>
> Then implement `integrations/anthropic_client.py` with a `call(prompt, model="claude-sonnet-4-5", max_tokens=4096)` function. Use the `anthropic` SDK. Add a single retry on rate-limit errors with exponential backoff.
>
> Then implement `agents/interpreter.py`. The `interpret(doc_id)` function fetches the doc text, constructs the extraction prompt (write the prompt as a docstring or module-level constant for reviewability), calls the LLM, parses the JSON response into `DocExpectations`, validates, and returns.
>
> Write unit tests BEFORE the parsing logic. Mock the LLM response with fixture JSON strings — do NOT test that the LLM produces correct output; test that we correctly parse and validate whatever it returns. Add one integration test marked `@pytest.mark.integration` that actually calls the LLM against a short fixture string.
>
> Add a Typer CLI command `interpret` in `cli.py` that takes a doc ID and an output path, runs the agent, and writes YAML. Do not auto-create the demo doc's expectations file — Joe will run the CLI manually and review the output.

---

## Phase 3 — Navigator agent

**Goal**: Given a `DocExpectations`, the Navigator drives Playwright through each expectation's described navigation, captures DOM state and screenshots at each step, and produces a `DocCapture` artifact.

This is where the existing bot's reusable code lands. The Phase 0 `REUSE.md` tells us how.

### Work

- [ ] Based on `REUSE.md`, either:
  - Copy specific functions from the existing bot into `agents/navigator.py` (preserve attribution comments)
  - Or import as a package if the existing repo can be made pip-installable
- [ ] Implement `agents/navigator.py`:
  - `capture(expectations: DocExpectations, doc_config: dict) -> DocCapture`
  - Internally: launch Playwright, authenticate to Okta tenant, for each expectation: navigate per the `navigation` breadcrumbs, capture DOM snippet for the relevant region, capture accessibility tree labels, screenshot the page, attach to `CapturedState`
- [ ] DOM capture must be **structured**: extract all visible text labels (button text, tab text, section headers, field labels) with their selectors, not just a full HTML dump. This is the substrate the Comparator uses.
- [ ] Screenshots saved to `runs/<run_id>/screenshots/<step_id>.png`
- [ ] Run headless with Playwright's built-in video recording enabled via `browser.new_context(record_video_dir='runs/<run_id>/playwright/', record_video_size={'width': 1280, 'height': 720})`. This produces clean .webm footage of the admin console navigation that becomes the source material for the demo video in Phase 8. Headless + video recording works identically on EC2 Ubuntu (where the bot is built and run) and on a developer laptop — no display server needed.
- [ ] CLI command: `drift-bot capture <expectations-path> --doc-config <path> --out <path>`

### Tests

- `tests/unit/test_navigator.py`:
  - Test selector resolution logic (given an accessibility tree, find labels matching expected text)
  - Test screenshot path generation
  - Test CapturedState construction from mock Playwright objects (use `pytest-playwright`'s fixtures or hand-rolled mocks of the page interface)
- No Playwright-against-real-Okta in unit tests. One integration test launches Playwright against the demo tenant and asserts captures non-zero results — manual execution only, not in CI.

### Done-criteria

- Running the agent against the demo doc's expectations produces a `DocCapture` containing labels actually visible on the live Okta admin console
- Specifically: the capture for the Governance Engine step contains `Entitlement Engine` as a captured label at the location where the doc expects `Governance Engine`
- Screenshots are written to disk with sane filenames

### Deferred

- Robust error recovery (retry on stale element, network errors, etc.) — for the demo, manual re-run is fine
- Scheduled cloud execution as a service (Lambda, ECS, etc.)
- Parallel multi-step navigation
- A `--headed` developer-convenience flag (useful when iterating on selectors locally; not needed for the demo since recording is from .webm files)

### Claude Code task brief for Phase 3

> Read BUILD_PLAN.md Phase 3 and REUSE.md. Decide on the integration strategy with the existing bot per REUSE.md's recommendation. If lifting specific functions, copy them into `agents/navigator.py` with a comment at the top of each function attributing source: `# Adapted from joevanhorn/okta-terraform-demo-template:<filename>:<lines>`.
>
> Implement `agents/navigator.py` with a `capture(expectations, doc_config)` function as specified. The capture process for each expectation:
>
> 1. Navigate the browser following the `navigation` breadcrumbs (this may require crawling tabs, menus, etc.; use the existing bot's patterns if applicable)
> 2. Once at the expected location, extract all visible text labels with selectors using a deterministic approach: query for buttons, headings, tabs, labels, navigation links; collect their text content and selectors
> 3. Take a screenshot of the current viewport
> 4. Construct a `CapturedState` with all of the above
>
> Default to headless mode with Playwright video recording enabled. Construct the browser context with `record_video_dir='runs/<run_id>/playwright/'` and `record_video_size={'width': 1280, 'height': 720}` so each step's navigation produces a .webm file. The captured .webm files are the source footage cut into the demo video in Phase 8. This works on EC2 Ubuntu without any display server. Optionally add a `--headed` developer-convenience flag for local selector debugging — but the demo never uses it.
>
> Write unit tests BEFORE implementation for the deterministic parts: label extraction from mock DOM, selector resolution, CapturedState construction. Do not mock Playwright internals in tests; test the data-shaping functions in isolation.
>
> Add the Typer CLI `capture` command. The output is a single JSON file containing the full `DocCapture` plus a `screenshots/` directory.

---

## Phase 4 — Comparator agent

**Goal**: Given a `DocExpectations` and a `DocCapture`, produce a `DriftReport` listing every drift finding with class, severity, confidence, and suggested correction.

### Work

- [ ] Implement `agents/comparator.py`:
  - `compare(expectations: DocExpectations, capture: DocCapture) -> DriftReport`
  - For each `Expectation`, find the matching `CapturedState` by `step_id`
  - For each `UILabel` in the expectation:
    - **Deterministic text match** against `accessible_labels` in the capture — if found, no drift
    - If not found, **proximity check**: is there a label of similar `semantic_role` in the same region of the page? Use the bounding box and DOM proximity heuristics
    - If proximity check finds a candidate: invoke **vision LLM** with the screenshot region and the question: *"The documentation expects a label '\<expected\>' here. Is there a label that appears to be a renamed equivalent? If yes, classify the change."*
    - Construct a `DriftFinding` with appropriate `drift_class`, `confidence`, and `suggested_correction`
- [ ] Drift class classification rules:
  - `label_rename`: same semantic role, same location, different text, high confidence
  - `terminology_update`: text differs but is a recognized product/feature rename — use a small static dictionary or LLM judgment
  - `broken_link`: URL in expectation no longer reachable (HTTP probe)
  - `procedural_change`: expected step's labels not found anywhere, but other steps still resolve
  - `outcome_change`: post-action state differs from expected
  - `ambiguous`: any case the heuristics can't confidently classify → routes to human review
- [ ] `auto_merge_eligible` is True only when: `drift_class in {label_rename, terminology_update, broken_link}` AND `confidence >= 0.85`

### Tests

- `tests/unit/test_comparator.py`:
  - Test text-match logic against fixture captures — covers the deterministic majority of the agent
  - Test drift classification given mock comparison results
  - Test confidence scoring boundaries
  - Test `auto_merge_eligible` gating logic
- One integration test calling the vision LLM with a real screenshot fixture and asserting it produces a valid `DriftFinding`

### Done-criteria

- Running against demo doc's expectations + captured state produces a `DriftReport` with the Governance Engine → Entitlement Engine drift correctly classified as `label_rename` with high confidence and `auto_merge_eligible: True`
- The second seeded drift is also detected; if procedural, classified as `procedural_change` with `auto_merge_eligible: False`
- A non-drifted step produces no false positive findings

### Deferred

- Sophisticated DOM proximity heuristics — start with simple "same parent container" and iterate
- Historical confidence calibration
- Multi-shot vision LLM prompts

### Claude Code task brief for Phase 4

> Read BUILD_PLAN.md Phase 4. Implement `agents/comparator.py` with the `compare(expectations, capture)` function. Begin with the deterministic text-match path: for each expected label, check if it appears in the captured accessible_labels for the matching step. No drift if found.
>
> When a label is NOT found, look in the same DOM region (same parent or sibling container based on the selector) for other labels of the same semantic_role. If found, construct a candidate rename. For confidence above 0.85, no vision LLM is needed — trust the deterministic proximity match. For ambiguous cases (multiple candidates, semantic role mismatch), invoke the vision LLM with the screenshot and an explicit prompt asking for classification.
>
> Implement the static product-rename dictionary as a simple Python dict in `agents/comparator.py`. Include entries Joe will provide; for now, hard-code `{"Governance Engine": "Entitlement Engine"}` as a known mapping. The Comparator should use this dictionary to bump confidence on a `terminology_update` match.
>
> Write unit tests for: text-match, proximity match, dictionary lookup, drift class assignment, confidence scoring, and `auto_merge_eligible` logic. Mock the vision LLM in unit tests; add one integration test that calls vision LLM against a real fixture screenshot.
>
> Add the Typer CLI `compare` command taking an expectations path and a capture path, producing a `DriftReport` JSON file.

---

## Phase 5 — Annotator agent

**Goal**: Given a `DriftReport`, write annotations back to a copy of the source Google Doc — comments for all findings, suggested edits for non-auto-merge findings, and direct edits for auto-merge findings (deferred to Phase 6 for the auto-merge policy gate).

### Work

- [ ] Implement `integrations/google_drive.py` — wrapper for copying a Drive file and managing the copy's permissions
- [ ] Extend `integrations/google_docs.py` with:
  - `insert_comment(doc_id, text_range, comment_body) -> comment_id`
  - `insert_suggested_replacement(doc_id, text_range, replacement) -> suggestion_id`
  - `apply_replacement(doc_id, text_range, replacement) -> revision_id` (for auto-merge mode)
- [ ] Implement `agents/annotator.py`:
  - `annotate(report: DriftReport, source_doc_id: str, mode: Literal["copy", "in_place"]) -> AnnotationResult`
  - `mode="copy"` for the demo: copy the source doc, then write annotations to the copy
  - `mode="in_place"` for the auto-merge story: write annotations directly to the source
  - For each finding: insert a Drive comment with structured body (drift class, evidence, suggested correction); insert a Google Docs suggested edit if the finding has a `suggested_correction`
- [ ] Text range resolution is the tricky part: the doc's text is plain text but the LLM-extracted expectations don't carry doc offsets. Solution: when the Interpreter runs, capture the source text snippet for each expectation. The Annotator searches for that snippet in the live doc to locate the range.

### Tests

- `tests/unit/test_annotator.py`:
  - Test comment body formatting given a `DriftFinding`
  - Test text range resolution: given a doc text and a target snippet, find the range
  - Test handling of multiple ranges matching the same snippet (use the first; warn)
- Integration test: run end-to-end against a throwaway demo doc copy, assert that comments and suggestions appear in the API responses

### Done-criteria

- Running the agent against the demo `DriftReport` produces a Drive copy of the demo doc with:
  - One comment at the Governance Engine text location, body explaining the rename
  - One suggested edit replacing `Governance Engine` with `Entitlement Engine`
  - Equivalent annotations for the second seeded drift
- All comments and suggestions are visible in the Google Docs UI when the doc is opened

### Deferred

- Smart text-range resolution when snippets appear multiple times in a doc — for the demo, ensure seeded drift terms are unique
- Comment threading
- Notification to doc owner via Docs' built-in mention/email

### Claude Code task brief for Phase 5

> Read BUILD_PLAN.md Phase 5. The Annotator has two sub-problems: text range resolution and the Google Docs API write calls. Start with text range resolution as pure-function logic with unit tests: given a doc's full text and a target snippet (the prose chunk captured during interpretation), return the `(start_index, end_index)` of the snippet.
>
> Extend `integrations/google_docs.py` to add the three API operations: insert comment, insert suggested replacement, apply replacement. Use the `batchUpdate` endpoint for all write operations. Read the Google Docs API reference carefully — suggested edits use a specific request type and require the document to be in "suggesting" mode for some operations.
>
> Implement `agents/annotator.py` per the spec. For Phase 5, default mode to `"copy"` — always copy the source doc first and annotate the copy. Auto-merge (`"in_place"`) is wired but should be gated by the Reviewer in Phase 6.
>
> Unit-test the comment-body formatter and the text-range resolver with deterministic fixtures. Add an integration test marked `@pytest.mark.integration` that against a real test doc validates the API calls produce the expected artifacts.
>
> Add Typer CLI: `drift-bot annotate <report-path> --doc-id <id> --mode copy|in_place`.

---

## Phase 6 — Reviewer and notifications

**Goal**: Policy engine decides per-finding whether to auto-merge or hold for review. Slack notification fires on every action. Summary report doc is generated.

### Work

- [ ] Implement `policy/default.yaml`:
  ```yaml
  auto_merge_classes:
    - label_rename
    - terminology_update
    - broken_link
  confidence_threshold: 0.85
  require_human_review_classes:
    - procedural_change
    - outcome_change
    - ambiguous
  ```
- [ ] Implement `agents/reviewer.py`:
  - `review(report: DriftReport, policy: dict, annotation_result: AnnotationResult) -> ReviewOutcome`
  - For each finding: consult policy, decide action, accumulate outcomes
- [ ] Implement `integrations/slack.py` — `post_message(webhook_url, blocks: list[dict])` using Slack's incoming webhook with Block Kit
- [ ] Slack message format (one per run, batched):
  - Header: doc name + run timestamp
  - Section per finding: class, severity, confidence, action taken (auto-merged | suggestion posted | held for review), link to evidence
  - Footer: "Revert" buttons linking to Drive version history for auto-merged changes (Drive doesn't support direct revert via URL, so link to the version history pane)
- [ ] Summary report generation:
  - `agents/reviewer.py` writes `runs/<run_id>/summary_report.md` with a human-readable rundown of the run
  - Optional: create a Google Doc version of the summary in a `/drift-reports/` Drive folder

### Tests

- `tests/unit/test_reviewer.py`:
  - Test policy lookup: given a finding and policy, return correct action
  - Test confidence-threshold gating
  - Test Slack message construction (assert correct Block Kit structure)
  - Test summary report markdown generation

### Done-criteria

- Running the full pipeline (Interpreter → Navigator → Comparator → Annotator → Reviewer) produces:
  - Auto-merged changes in the source doc for `label_rename` findings above threshold
  - Suggestions in a copied doc for `procedural_change` findings
  - A Slack message in the configured channel with both items
  - A `summary_report.md` in the run directory
- The Slack message renders correctly in the destination channel

### Deferred

- Configurable per-doc policy overrides
- Multi-channel notifications (email, GitHub Issues, etc.)
- A real revert mechanism in Slack (just link to version history for now)
- The dashboard UI mentioned in Beat 5 of the demo

### Claude Code task brief for Phase 6

> Read BUILD_PLAN.md Phase 6. Implement the policy YAML, `agents/reviewer.py`, `integrations/slack.py`, and summary report generation as specified.
>
> The Reviewer is the orchestration glue between Comparator output and Annotator action. It does not perform the annotation itself; it decides the `mode` parameter passed to the Annotator per finding and then invokes the Annotator with that decision.
>
> Implement the Slack message using Block Kit JSON. Test the block structure with unit tests; do not actually post to Slack in unit tests. Add one integration test that posts to a test channel (gated by an environment variable).
>
> Generate the summary report as markdown. The format should be clean enough to also serve as the basis for a Google Doc — for now, just write the markdown file. Phase 8 can extend this to create the Google Doc if there's time.
>
> Add Typer CLI: `drift-bot review <report-path> --doc-id <id> --policy <policy-path>`.

---

## Phase 7 — Orchestration

**Goal**: A single CLI command runs the full pipeline end-to-end against a doc config and produces all artifacts. This is what gets run on camera for the demo video.

### Work

- [ ] Implement the top-level `drift-bot run <doc-config-path>` command in `cli.py`:
  1. Load doc config
  2. Run Interpreter → write/load expectations
  3. Run Navigator → capture state
  4. Run Comparator → produce findings
  5. Run Reviewer → invoke Annotator per finding, post Slack notification, write summary
  6. Print run directory path and Slack message URL
- [ ] Generate a `run_id` (UUID4 short form) for each run; create `runs/<run_id>/` and place all artifacts there
- [ ] Add structured logging at each phase boundary so the terminal output is video-friendly: emojis or color, clear phase headers, timing
- [ ] Add `--dry-run` flag that runs everything but skips the Annotator write-back

### Tests

- `tests/integration/test_end_to_end.py`:
  - Full pipeline against the demo doc, against the live Okta tenant
  - Asserts: `DriftReport` contains the two expected findings, Annotator creates the expected comments/suggestions, Slack message is posted (gated by env var)
  - Marked `@pytest.mark.integration`, manual execution only

### Done-criteria

- `uv run drift-bot run docs/governance-engine-lab/doc-config.yaml` runs the full pipeline cleanly in under 3 minutes wall-clock
- The Slack message arrives with both findings
- The source doc shows the auto-merged change for the Governance Engine rename
- A copy of the source doc exists in Drive with the suggested edits and comments for the second drift
- The `summary_report.md` in the run dir is readable and accurate

### Deferred

- Resumable runs (if Navigator fails halfway, no resume — just re-run)
- Parallel doc execution
- Run history queries

### Claude Code task brief for Phase 7

> Read BUILD_PLAN.md Phase 7. Implement the top-level `run` CLI command in `cli.py` that orchestrates the full pipeline. Use the existing per-agent CLI commands as the underlying implementation — call the functions directly, do not shell out to the CLI.
>
> Add structured terminal logging with clear phase headers ("PHASE 1: INTERPRETING DOC", "PHASE 2: CAPTURING STATE", etc.) and timing. Use the `rich` library if you want color/formatting, otherwise plain print is fine. Optimize the terminal output for video recording: it should be readable and visually interesting when paused.
>
> Add `--dry-run` flag. Add a `--run-id` flag for forcing a specific run ID (useful for re-running into the same directory during development).
>
> Write the integration test in `tests/integration/test_end_to_end.py`. Mark it `@pytest.mark.integration` and skip if `INTEGRATION_TESTS=1` is not set.

---

## Phase 8 — Demo video production

**Goal**: A 2-minute video demonstrating the full loop. This is the actual deliverable.

### Work

- [ ] Write the video script (target 250–300 words for a 2-minute voiceover at normal pace)
- [ ] Storyboard the five beats:
  - 0:00–0:15 — Problem: doc says "Governance Engine", admin console says "Entitlement Engine" (cut between them)
  - 0:15–0:45 — Detection: bot runs (terminal output captured separately), Playwright's recorded .webm footage shows the admin console navigation, Comparator finds drift
  - 0:45–1:15 — Auto-repair: cut to Drive, watch the doc update, version history pane opens showing the bot as the editor, Slack message lands
  - 1:15–1:45 — Judgment: the second drift case routes to suggested-edit mode, show the comment and suggestion in the doc copy
  - 1:45–2:00 — Vision: dashboard mock + pitch sentence
- [ ] Build the dashboard mock as a static HTML page styled to match the proposal deck — fake numbers, fake "47 docs under continuous validation"
- [ ] Record screen captures of each beat individually. Re-take as needed.
- [ ] Record voiceover separately for clean audio
- [ ] Edit in a video editor (Loom, ScreenStudio, Final Cut, etc.)
- [ ] Add lower-third labels at scene transitions to help viewers follow
- [ ] Export at 1080p, target file size under 50MB

### Done-criteria

- A 2-minute video file exists
- It tells the full story without requiring narration from Joe
- Every artifact shown in the video is real (Slack message, doc changes, version history) except the dashboard mock and any compositing
- The video is sharable as a file or via a hosted link

### Deferred

- Multiple language tracks
- Closed captions (do as a stretch if time allows; helps for sharing on internal Slack)
- Animated agent diagrams — use the static mermaid from the proposal if needed

### Claude Code task brief for Phase 8

> Phase 8 is mostly non-code work. Help Joe by: (1) Drafting the voiceover script targeting 250 words and the five-beat structure from BUILD_PLAN.md. (2) Building a static dashboard mock HTML page that visually matches `documentation-drift-detection-proposal.html` (dark navy header, the orange/teal accent palette) showing "47 docs validated", "Last scan 6 hours ago", "3 drifts detected this week (2 auto-repaired, 1 awaiting review)", and a table of recent runs. Save it at `demo/dashboard_mock.html`. (3) Creating a `demo/SHOOTING_NOTES.md` with a per-beat shot list, what to capture, what to cut, and re-take triggers. The admin console footage for Beat 2 comes from the Playwright .webm recordings in `runs/<run_id>/playwright/` — not from a live screen capture of a browser window — so the shooting notes should explicitly call out which .webm files map to which video beats and how they're composited with separately-captured terminal output.

---

## Open decisions

These will block specific phases if not resolved before the relevant phase starts:

- **Phase 1**: Which second drift instance do we seed — procedural or terminology? Pick whichever produces the cleanest video moment.
- **Phase 1**: Exact text of the seeded lab guide — write it new or adapt an existing internal doc?
- **Phase 3**: After REUSE.md exists, the integration strategy (lift functions vs treat as inspiration) is committed.
- **Phase 6**: Slack channel for demo notifications — recommend `#enablement-drift-demo` to contain noise.
- **Phase 8**: Voiceover by Joe directly or AI-generated (ElevenLabs etc.)?

---

## Claude Code working notes

How to use this plan with Claude Code:

1. **Start fresh per phase.** Open Claude Code, paste the Phase N task brief, point it at the repo, let it work. Do not paste the whole BUILD_PLAN — too much context dilutes the focus.
2. **Verify before advancing.** Run the phase's tests, check the done-criteria yourself, then commit before moving to the next phase. Per the coding rules: commit frequently.
3. **Use a phase branch.** `git checkout -b phase-N-<short-name>` for each phase. Merge to main only when done-criteria are met.
4. **When Claude Code deviates from the plan**: if the deviation is technically sound (e.g., uses a better library), accept. If it's making the design more complex than the plan calls for, push back and reference YAGNI.
5. **The Anthropic API call pattern in `integrations/anthropic_client.py`** is shared by Interpreter and Comparator. Keep it minimal; both agents own their prompts.

---

## What success looks like at the end of the week

- The repo runs end-to-end against the demo doc
- The 2-minute video is recorded and exported
- The video is shareable internally with no further work needed
- The codebase is in a state where, after the hackathon, the production rollout from the proposal could begin without rewriting the foundations
