# CloudQA Task 2

CloudQA Task 2 is a Python application that turns a plain-English browser test into a
bounded Playwright run. A constrained planner interprets the current page snapshot,
the browser engine resolves snapshot-scoped element IDs, and assertions are evaluated
against the live page state. The repository also includes offline tests using local
HTML fixtures and a scripted fake planner.

## Requirements and installation

- Python 3.14 (Python 3.14.6 is used in the current workspace)
- Playwright with its Chromium browser
- Google Gen AI SDK, Pydantic, python-dotenv, and pytest

From the project root, activate the existing virtual environment in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the declared dependencies and Chromium if they are not already installed:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Run the offline test suite with:

```powershell
python -m pytest -q
```

## Environment configuration

Copy `.env.example` to `.env` and set `GEMINI_API_KEY` locally. The example uses
`GEMINI_MODEL=gemini-3.6-flash`; change it only to a model available to your API
project. Do not commit `.env` or paste API keys into source files, prompts, reports,
or issue logs.

Useful settings:

| Variable | Purpose | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini authentication key (local environment only) | unset |
| `GEMINI_MODEL` | Model used by the Gemini provider | `gemini-3.6-flash` in `.env.example` |
| `GEMINI_TIMEOUT_MS` | Gemini HTTP transport timeout, in milliseconds | `180000` |
| `MAX_PLANNER_CALLS` | Maximum provider calls per run | `4` |
| `PLANNER_MAX_STEPS` | Maximum actions in a plan | `10` |
| `PLANNER_CORRECTION_ATTEMPTS` | Attempts to repair invalid planner output | `2` |
| `BROWSER_HEADED` | Show Chromium instead of running headlessly | `false` |
| `BROWSER_NAVIGATION_TIMEOUT_MS` | Navigation timeout, in milliseconds | `30000` |
| `BROWSER_ACTION_TIMEOUT_MS` | Action timeout, in milliseconds | `5000` |
| `ALLOWED_SECRET_NAMES` | Comma-separated environment variable names permitted as secrets | empty |

For example, set `ALLOWED_SECRET_NAMES=LOGIN_EMAIL,LOGIN_PASSWORD`, then define
those environment variables locally. Plans can reference allowed names, but secret
values are resolved only by the browser executor and are masked in output.

## Run a plain-English browser test

Use the fake provider for a no-network local smoke run:

```powershell
python main.py --url "file:///C:/path/to/CloudQA-Task2/fixtures/sample_login.html" --test-case "Verify the sign in button is visible." --provider fake
```

With Gemini configured, use:

```powershell
python main.py --url "https://example.com" --test-case "Verify that the page heading is displayed." --provider gemini
```

The CLI also prompts for the URL and test case when omitted. `--headed` displays the
browser; `--headless` forces headless mode. Exit codes are `0` for PASS, `1` for a
test outcome requiring attention (including FAIL, ambiguity, or safety blocking), and
`2` for configuration or execution errors.

## Architecture and workflow

1. `browser/` opens the target and builds a compact snapshot of visible interactive
   elements. Each element ID is valid only for the snapshot that produced it.
2. `ai/` builds the planner prompt from the test case, current snapshot, and allowed
   secret names. The Gemini provider requests structured JSON using the Pydantic plan
   schema. Invalid JSON/schema output is corrected within the configured attempt
   limit; offline tests use `FakeProvider`.
3. `engine/` screens unsafe actions, validates and executes the plan, reinspects the
   page after changes, and checks assertions against the browser.
4. `reporting/` writes sanitized JSON, HTML, and text summaries.

The runner reports PASS only when at least one browser assertion was evaluated and all
evaluated assertions passed. Payment/purchase/booking/delete/cancel actions and
security-check bypass requests are blocked. Input values are not included in page
snapshots; credentials and API keys are not included in reports.

## Reports

Each run writes `.json`, `.html`, and `.txt` files under `reports/`. Failure,
ambiguity, and execution-error runs may also save a screenshot there. Generated report
files are ignored by Git; `reports/.gitkeep` preserves the directory.

## Limitations and error handling

- Element references are snapshot-scoped; the planner must use IDs from its supplied
  snapshot, and the runner refreshes the snapshot after relevant page changes.
- The planner is bounded by step, correction, and API-call limits. A malformed or
  incomplete plan is rejected or returned as a planner error rather than executed.
- Gemini timeout, connection, authentication, and quota failures are surfaced as
  controlled execution errors. The application does not add a second automatic
  provider retry loop.
- The project does not guarantee success on arbitrary third-party sites; page
  structure, authentication, network access, and site policies can prevent a run.
- Live network/Gemini tests are opt-in and skipped by default. Offline test results do
  not establish that a live API request or third-party site test has passed.
