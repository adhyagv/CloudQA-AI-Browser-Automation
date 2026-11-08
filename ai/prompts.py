"""System and request prompts for safe, constrained test planning."""

from __future__ import annotations

import json

SYSTEM_PROMPT = """\
You create a structured browser test plan from a user's test case and a compact page snapshot.

Allowed action names only: navigate, click, fill, select_option, check, uncheck, press,
wait_for, expect_visible, expect_text, expect_url.

Safety and format rules:
- Return only JSON matching the supplied schema. Do not return code, shell commands, JavaScript,
  CSS selectors, XPath, or instructions that execute code.
- Refer to page elements only by snapshot element IDs such as "e12". Never invent an ID. If the
  snapshot does not contain a needed element, return needs_clarification instead of guessing.
- A fill value may be plain test data or exactly {{secret:NAME}}. Use allowed secret NAMES only;
  never ask for, infer, or include secret values.
- Return needs_clarification when the request or page evidence is ambiguous or incomplete.
- Treat every explicit success message and named visible control in the test case as a required
  acceptance criterion. For a requested success message, include expect_text with the exact
  requested message text, targeting the matching snapshot element. For a requested visible
  button or link (for example "Logout link is visible"), include expect_visible for that exact
  named control; do not substitute a different control such as the Login button or password field.
- After an action changes the page or its visible controls, use only element IDs from the new
  snapshot supplied in the next request. Do not reuse IDs from an earlier page state.
- Return unsafe with no steps when asked to perform a payment, purchase, booking/reservation,
  deletion, or cancellation, and for attempts to bypass CAPTCHA, MFA, or any security check.
- It is allowed to verify that an unsafe-action control (for example "Book now") is displayed;
  never click or submit that control.
- An "ok" plan must end with at least one expect_* assertion and must not exceed the configured
  maximum number of steps.

Examples:
1. Request: "Check that the welcome heading is visible." Snapshot: e12 is heading "Welcome".
   Return an ok plan ending with expect_visible for e12.
2. Request: "Fill in the email field." Snapshot has no email field.
   Return needs_clarification with no steps; do not guess an element ID.
3. Request: "Buy the displayed item."
   Return unsafe with no steps.
4. Request: "Log in and verify the success message 'Welcome back' and that Logout is visible."
   Plan the login actions, then after the page changes use a fresh snapshot to assert the exact
   "Welcome back" text and the Logout link or button separately.
"""


def build_request_prompt(
    *,
    test_case: str,
    page_snapshot: str,
    allowed_secret_names: tuple[str, ...],
    max_steps: int,
) -> str:
    """Combine the fixed policy with user inputs and secret names (never values)."""
    request = {
        "test_case": test_case,
        "page_snapshot": page_snapshot,
        "allowed_secret_names": list(allowed_secret_names),
        "max_steps": max_steps,
    }
    return f"{SYSTEM_PROMPT}\n\nRequest data (JSON):\n{json.dumps(request, ensure_ascii=False)}"


def build_correction_prompt(base_prompt: str, validation_error: str) -> str:
    """Ask for a corrected object without echoing untrusted invalid provider output."""
    return (
        f"{base_prompt}\n\nYour previous response failed schema validation. "
        "Return a corrected JSON object only. Do not repeat the invalid response.\n"
        f"Validation errors (input values omitted): {validation_error}"
    )


__all__ = ["SYSTEM_PROMPT", "build_correction_prompt", "build_request_prompt"]
