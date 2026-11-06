# CloudQA Task 2

A milestone-based AI-assisted, natural-language browser test automation project. The current implementation includes a constrained AI test planner and a local-fixture Playwright browser engine.

## Technology stack

- Python 3.14.6 (the version verified for this workspace)
- Playwright for browser automation
- Google Gen AI SDK for a later milestone
- Pydantic for data validation
- python-dotenv for local configuration
- pytest for testing

## Setup

From the project root, activate the existing virtual environment in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the pinned dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install the Playwright Chromium browser:

```powershell
python -m playwright install chromium
```

To configure Gemini for a future manual run, copy `.env.example` to `.env` and set `GEMINI_API_KEY` and a currently supported `GEMINI_MODEL` locally. Choose a model listed in the [official Gemini models documentation](https://ai.google.dev/gemini-api/docs/models); the model name is intentionally not hardcoded. Never commit `.env` or share its contents. AI planner tests use only `FakeProvider`; browser tests use local fixture files, with no live API calls or websites.

The browser uses headless Chromium by default. `BROWSER_HEADED=true` opts into a visible browser; navigation/action timeouts and snapshot element/text limits are configurable in `.env`. Add comma-separated secret names to `ALLOWED_SECRET_NAMES` and set each corresponding secret as a separate local environment variable. Secret values are resolved only at execution time and are masked from results.

Run the offline tests with:

```powershell
python -m pytest -q
```

## Project structure

```text
CloudQA-Task2/
├── ai/          # Planner, provider interfaces, prompts, and schemas
├── browser/     # Chromium manager, page snapshots, locator resolver, and actions
├── config/      # Environment-backed settings
├── engine/      # Assertion evaluation and action safety checks
├── fixtures/    # Static local HTML browser fixtures
├── models/      # Strict actions, plans, and step results
├── reporting/   # Future reporting components
├── reports/     # Generated reports (except .gitkeep, ignored)
├── tests/       # Fake-provider and local Chromium tests
├── main.py      # Placeholder application entry point
├── .env.example
├── .gitignore
└── requirements.txt
```

## Development status

Milestone 4: AI planning, local-fixture browser execution, snapshot-based semantic locator resolution, assertion evaluation, and initial unsafe-action blocking. The browser engine does not navigate to live sites during automated tests. Full test orchestration and final report generation remain future work.
