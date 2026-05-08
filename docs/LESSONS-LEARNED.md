# Lab Screenshot Bot — Lessons Learned

## Project Summary
Built an LLM-powered browser automation bot that follows Okta lab guides, navigates the Admin Console, executes lab steps, and captures screenshots. The bot uses Claude (via Bedrock/LiteLLM) to comprehend guides, reason about UI state, and make decisions — essentially robotic process automation with human-like reasoning.

**Timeline**: ~4 days of iterative development and testing
**Final result**: All 6 sections of a complex brute force/MFA lab guide completed in ~54 iterations

---

## Architecture Decisions

### Two-Pass System (Record + Select)
- **Pass 1**: LLM-driven agent navigates through the guide, capturing a screenshot after every action
- **Pass 2**: Vision model picks the best frame for each `[SCREENSHOT]` marker from the gallery
- **Why**: Decouples navigation from screenshot selection. The agent doesn't need to know which frame is "best" — it just needs to reach the right pages. Vision handles the matching later.

### Section-by-Section Execution
- **Comprehension phase**: LLM reads the entire guide and produces a structured plan with sections, each having a title, goal, steps, success condition, and context (which tab/page)
- **Execution phase**: Each section runs independently with its own conversation, preventing context pollution between sections
- **Why**: A single long conversation loses focus. The bot would re-read steps, forget progress, and loop. Section isolation gives each task a fresh context with a clear goal.

### Reasoning-First Prompts (Not Rule-Based)
- Early versions used rigid rules: "don't click the same button twice", "watch for dialogs", etc.
- Final version uses chain-of-thought: "Where am I? What step am I on? What should I do? What do I expect?"
- **Why**: Rules are brittle and guide-specific. Reasoning generalizes. The bot figured out placeholder vs. filled values, recognized dialog results, and adapted to unexpected states — all from reasoning, not rules.

---

## Key Technical Challenges

### 1. The LLM Was Navigating Blind
**Problem**: The bot had browser control tools (click, fill, navigate) but never saw the page. It only got a text dump of interactive elements from `get_page_state`.

**Solution**: Send a screenshot to the LLM after every iteration. The screenshot is ephemeral (not accumulated in message history) to avoid blowing up the context window.

**Lesson**: Vision feedback is the single highest-impact feature. Without it, the bot can't verify actions worked, understand page layout, or identify what changed. With it, the bot reasons about what it sees like a human would.

### 2. Dialog/Modal Handling (Okta SimpleModal)
**Problem**: Okta uses multiple overlapping dialog layers — MUI dialogs for the Edit Rule form, and SimpleModal (`#simplemodal-container`) for confirmation prompts. The bot kept clicking elements behind the dialog overlay.

**Solution**:
- Dialog-aware `get_page_state` that scopes element queries to the topmost dialog
- Dialog-scoped clicks that try the dialog first, then fall back to normal clicks
- `#simplemodal-container` as highest priority in all dialog detection
- `scrollIntoViewIfNeeded()` before every click

**Lesson**: Any complex web app will have modal/overlay patterns. The bot needs to know which layer is "on top" and scope its interactions accordingly. Generic dialog detection (`[role="dialog"]`, `.modal`, `dialog[open]`) plus app-specific selectors (SimpleModal, MUI) covers most cases.

### 3. Selectize Custom Dropdowns
**Problem**: Okta's "User must authenticate with" dropdown is a Selectize control — a hidden `<select>` replaced by custom divs. Clicking the native `<select>` does nothing visible. There are also multiple Selectize controls on the same page (IF section and THEN section), and the bot kept clicking the wrong one.

**Solution**:
- Qualified selectors using the parent wrapper: `.selectize-wrapper:has(select[name='verificationMethod.type']) .selectize-input`
- `select_option` tool for native `<select>` elements (with Selectize change event dispatch)
- `inspect_element` tool so the bot can diagnose what type of control it's dealing with

**Lesson**: Custom dropdown controls are ubiquitous in enterprise web apps. The bot needs multiple strategies: native select_option, clicking custom divs, and the ability to inspect elements to determine which approach to use. Qualified selectors (scoped by parent/name) are essential when multiple similar controls exist on a page.

### 4. Sidebar Navigation (Expand/Collapse)
**Problem**: Okta's sidebar has expandable sections. Clicking "Security" doesn't navigate — it expands to show sub-items. The bot would click "Security" 15+ times without realizing it needed to then click "Authentication Policies."

**Solution**:
- Okta UI cheat sheet in the system prompt explaining the expand/collapse pattern
- Direct URL fallback after 2 failed sidebar clicks (`/admin/authentication-policies/app-sign-in`)
- Common admin URLs provided in the cheat sheet

**Lesson**: Complex navigation patterns need to be documented in the system prompt. But the documentation should be a cheat sheet, not rigid rules. The bot should try click-based navigation first, then fall back to direct URLs. This generalizes to any app with non-obvious navigation.

### 5. Repeated Action Loops
**Problem**: The bot would click "Execute" on the attack simulator, see the confirmation dialog, click Execute again (in the dialog), see results, close the dialog, then click Execute AGAIN — endlessly looping.

**Solution** (evolved through several iterations):
1. Hard-blocking repeated actions (too rigid, broke legitimate re-runs)
2. Soft warnings in progress log (ignored by LLM)
3. **Final**: Cumulative progress tracking with assertive messaging — "You have taken N actions. Do NOT repeat completed actions. Call section_complete NOW." Plus capturing success acknowledgments in the progress log.

**Lesson**: Prompt-based guardrails alone don't prevent loops. The bot needs to see its own history in a structured way. The progress log (showing what was done and what was observed) is more effective than rules about what NOT to do. The bot reasons from evidence ("I already saw 1/1 successful") rather than from rules ("don't click twice").

### 6. API Integration (Factor Enrollment)
**Problem**: The lab requires enrolling an MFA factor for a user, which normally needs a mobile device to scan a QR code. The bot can't interact with physical devices.

**Solution**:
- `browser_api` tool that makes Okta API calls
- Server-side Python `urllib` with SSWS token (not browser `fetch()` — CORS blocks it)
- Auto-activation: when a TOTP factor is enrolled and returns `PENDING_ACTIVATION` with a `sharedSecret`, the system automatically generates a TOTP code with `pyotp` and activates it
- Domain extraction from admin console tab URL (not the labs portal URL)

**Lesson**: Browser session cookies don't carry the same permissions as API tokens. For admin operations, a proper API token (SSWS) is essential. Server-side requests bypass CORS entirely. Auto-completing multi-step API flows (enroll → activate) saves iterations and prevents the bot from trying to compute TOTP in the browser.

### 7. Admin MFA Step-Up
**Problem**: Saving security-sensitive changes in Okta triggers an MFA push notification for the admin. The bot can't approve push notifications.

**Solution**:
- Detect MFA step-up prompts ("protected action", "step-up authentication", "•••" loading)
- Call `ask_human` to request the admin approve the push
- Human-in-the-loop via web UI chat panel AND terminal stdin
- Desktop notifications + audio beep + tab title flashing

**Lesson**: Some actions inherently require human intervention. The system needs graceful handoff — detect when human input is needed, notify prominently, wait patiently, and resume smoothly. The dual input approach (web UI + terminal) ensures the human can respond from wherever they're watching.

### 8. Viewport and Scroll Issues
**Problem**: Elements at the bottom of tall dialogs (like "Save anyway") were below the visible viewport. Clicks would either miss or hit the wrong element.

**Solution**:
- Increased viewport height to 1080px with `--window-size=1440,1200` launch arg
- `scrollIntoViewIfNeeded()` before every click (both dialog-scoped and normal)
- Smart scroll auto-detection: scrolls the dialog if one is open, then the main content area, then the window

**Lesson**: Visible browser mode (`headless=False`) has less usable space than headless due to browser chrome. Always scroll elements into view before clicking. For apps with scrollable containers (not just page scroll), auto-detect the right scroll target.

### 9. Closed Tab Recovery (MFA Tab Auto-Close)
**Problem**: After the admin approves the MFA push notification, Okta automatically closes the MFA challenge tab. The bot's `self.page` reference was pointing at the now-closed tab, causing a crash: "Target page, context or browser has been closed."

**Solution**:
- Page validity check at the start of every iteration and after every tool execution
- If the current page is closed, automatically switch to the best available tab (prefer admin console)
- Log the recovery so the bot knows it switched tabs

**Lesson**: Any action that opens a new tab can also close that tab unexpectedly (redirects, authentication flows, popups). The bot must never assume its current page reference is valid. Check before every interaction, and have a recovery strategy that picks the most relevant remaining tab.

### 10. Tab Awareness and New Tab Detection
**Problem**: The MFA step-up authentication opened in a new tab, but the bot was still looking at the original tab with a loading spinner. It spent many iterations watching the spinner without realizing the action it needed was in another tab.

**Solution**:
- Track tab count from the start of each section
- Alert the bot immediately when a new tab appears: "NEW TAB DETECTED — use list_tabs to check what opened"
- Include tab count in MFA detection hints
- Add "Did a new tab open?" to the standard post-action reasoning checklist

**Lesson**: Multi-tab awareness is critical for enterprise web apps. Authentication flows, policy saves, and link clicks can all open new tabs. The bot needs to monitor tab count changes and investigate new tabs proactively, not just when stuck.

### 11. Stuck Loop Detection and Escalation
**Problem**: The bot would repeat the same failed action (clicking a cookie dialog, retrying a selector) for 10+ iterations without trying a different approach or asking for help.

**Solution**:
- Detect when the last 3 progress entries target the same element
- Force escalation: "🚨 YOU ARE STUCK — call ask_human, check tabs, use inspect_element, or try something different"
- At 60% of iteration budget, suggest recovery strategies
- Explicit instruction to ignore irrelevant dialogs (cookies, promotions) rather than trying to dismiss them

**Lesson**: LLMs can get stuck in loops where they keep trying variations of the same failed approach. Explicit loop detection with forced escalation to `ask_human` breaks the cycle. The human can provide context the bot doesn't have ("ignore that dialog" or "the button is actually in a different tab").

### 12. Cookie/Consent Dialog Hallucination
**Problem**: The bot reported seeing a cookie consent dialog and spent 8 iterations trying to dismiss it, but the dialog wasn't actually visible on screen. It was either in the DOM but off-viewport, or a vision hallucination.

**Solution**:
- Auto-dismiss common cookie dialogs at section start (OneTrust, generic accept buttons)
- System prompt: "Ignore cookie/consent banners entirely. Use force:true if something blocks your click."
- Only interact with dialogs relevant to the current task

**Lesson**: LLMs can hallucinate UI elements, especially when `get_page_state` returns elements that are technically in the DOM but not meaningfully visible. The system prompt should explicitly tell the bot which types of dialogs to ignore, and encourage using `force: true` on clicks rather than trying to dismiss phantom overlays.

---

## What Worked Well

### 1. Comprehension → Execution Pattern
Breaking the guide into sections with goals and success conditions was the biggest architectural win. Each section runs with a clear objective, and the bot knows when to stop.

### 2. Vision Feedback
Sending screenshots to the LLM after every action transformed the bot from a blind script-executor into a reasoning agent that could verify its own actions and adapt.

### 3. inspect_element Tool
Giving the bot DevTools-like inspection capabilities let it self-diagnose click failures. It discovered that "Save anyway" was an `<input>` not a `<button>`, that elements were obscured by overlays, and that selectize controls hid the native `<select>`.

### 4. App-Specific Cheat Sheet
The Okta Admin Console cheat sheet in the system prompt — covering sidebar navigation, selectize dropdowns, policy rule actions, and direct URL fallbacks — saved dozens of iterations per run.

### 5. API Workaround Pattern
The comprehension phase identifying sections that can't be done in the browser but CAN be done via API — and executing those workarounds automatically — is a powerful pattern that generalizes beyond factor enrollment.

### 6. Mock Okta Server for Local Testing
Building a mock server that replicated the exact Okta UI patterns (Selectize, SimpleModal, collapsible sidebar) enabled rapid iteration without needing the real environment for every test.

---

## What Didn't Work

### 1. Rule-Based Prompts
Telling the bot "never click the same button twice" or "watch for dialogs" was too rigid. Rules conflict with each other, don't cover edge cases, and get ignored when the LLM's attention is elsewhere.

### 2. Soft Warnings for Loop Prevention
Adding "WARNING: you've repeated this action 3 times" to the screenshot message was completely ignored by the LLM. It kept clicking anyway.

### 3. Hard-Blocking Actions
Refusing to execute repeated actions (returning "REFUSED") worked for prevention but broke legitimate cases where the guide says to run the same action twice (attack simulator in section 1 and section 5).

### 4. Browser fetch() for API Calls
Using `page.evaluate(fetch(...))` for API calls hit CORS when crossing domains and couldn't use SSWS tokens. Server-side `urllib` was the right approach from the start.

### 5. CSRF Token Extraction
Spent significant time trying to extract CSRF/XSRF tokens from cookies, meta tags, and JavaScript globals for the Okta admin console. Turns out the admin console doesn't use CSRF cookies at all — and the real issue was hitting the wrong domain.

### 6. Giving the Full Guide to the LLM
Early versions dumped the entire guide (8000+ chars of narrative) into the prompt. The LLM got overwhelmed by the story and missed the actionable steps. Extracting just the numbered steps, headers, and markers was essential.

---

## Metrics (Final Successful Run)

| Section | Iterations | Key Actions |
|---------|-----------|-------------|
| Execute Attack | 7 | Click Execute, confirm dialog, observe 1/1 success |
| System Log | 6 | Switch tab, navigate to Reports > System Log, scroll |
| Configure MFA | 22 | Navigate to policy, edit rule, change dropdown, save, approve MFA |
| Enroll Factor (API) | 4 | GET user, POST factor (auto-activated) |
| Verify Attack | 7 | Re-run attack, observe 0/1 (MFA blocked it) |
| System Log (post-MFA) | 5 | Navigate to log, confirm all failures |
| **Total** | **54** | **36 frames captured, 6/6 sections complete** |

---

## Recommendations for TrainerAdvisor Bot

### Architecture
1. **Use the comprehension → section execution pattern**. Have the LLM read the full task/rubric first, produce a structured plan, then execute sections independently.
2. **Section isolation is critical**. Each section should have its own conversation with a clear goal and success condition. Don't let one section's context pollute another.
3. **Vision feedback is non-negotiable**. If the bot interacts with any UI, it must see screenshots. Text-only element lists are insufficient for complex enterprise UIs.

### Prompting
4. **Reasoning > Rules**. Teach the bot to think step-by-step, not follow a checklist. "Where am I? What should I do? What do I expect?" works better than "Don't click twice. Watch for dialogs."
5. **App-specific cheat sheets are essential**. Document the target app's navigation patterns, custom controls, and common gotchas. Keep it concise — a reference card, not a manual.
6. **Progress tracking prevents loops**. Show the bot what it has already done and observed. It reasons from evidence, not memory.

### Human-in-the-Loop
7. **Design for graceful handoff**. Some actions require human intervention (MFA, physical devices, ambiguous decisions). Detect these proactively and notify prominently.
8. **Multiple notification channels**. Desktop notification + audio + visual indicator in the UI. Users won't be staring at the screen the whole time.
9. **Dual input (UI + terminal)**. Let the human respond from wherever they're watching.

### API Integration
10. **Server-side API calls, not browser fetch()**. Browser-based API calls hit CORS and can't use service tokens. Use Python `urllib`/`requests` from the backend.
11. **Auto-complete multi-step API flows**. If step 1 returns data needed for step 2, do both automatically instead of making the LLM reason through it.
12. **API workarounds for physical-device operations**. Factor enrollment, device registration, and similar operations can often be done via API even when the UI requires physical interaction.

### Testing
13. **Build mock servers that replicate target app patterns**. Selectize dropdowns, modal overlays, collapsible navigation — test these locally before hitting the real environment.
14. **Each real test takes 10-15 minutes**. Optimize by fixing multiple issues per push, and validate locally first.

### Tools
15. **inspect_element is invaluable**. Let the bot examine DOM elements when clicks fail. It discovers tag types, obstructions, class names, and suggests better selectors.
16. **scroll + scrollIntoViewIfNeeded**. Enterprise apps have tall dialogs and scrollable containers. Always scroll elements into view before clicking.
17. **select_option for native dropdowns**. Playwright's `select_option()` is different from clicking. Custom dropdowns (Selectize, Select2) need both approaches.

---

## Technical Debt / Future Improvements

1. **Frame selector reliability**: The LLM sometimes returns empty responses for frame selection. The retry logic helps but a more robust approach (local CLIP-based matching?) could be more reliable.
2. **Iteration efficiency**: Some sections still burn iterations on sidebar navigation (2-4 failed clicks before falling back to direct URL). Could pre-detect the app and skip to direct navigation.
3. **Browser profile persistence**: Currently creates a fresh profile every run. Persisting the authenticated profile would save the manual auth step.
4. **Parallel section execution**: Sections that don't depend on each other could theoretically run in parallel (e.g., System Log review doesn't block the next section).
5. **Org state management**: The bot should be able to detect and reset org state (policies, factors) at the start of a run, not rely on manual cleanup.
