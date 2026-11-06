# CloudQA Task 2

A milestone-based AI-assisted, natural-language browser test automation project. The current implementation includes a constrained AI test planner; browser execution and reporting workflows are not implemented.

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

To configure Gemini for a future manual run, copy `.env.example` to `.env` and set `GEMINI_API_KEY` and a currently supported `GEMINI_MODEL` locally. Choose a model listed in the [official Gemini models documentation](https://ai.google.dev/gemini-api/docs/models); the model name is intentionally not hardcoded. Never commit `.env` or share its contents. The automated test suite uses only `FakeProvider` and does not make API calls.

Run the offline tests with:

```powershell
python -m pytest -q
```

## Project structure

```text
CloudQA-Task2/
├── ai/          # Planner, provider interfaces, prompts, and schemas
├── browser/     # Future browser automation components
├── config/      # Environment-backed settings
├── engine/      # Initial plan safety screening
├── fixtures/    # Test fixtures
├── models/      # Strict action and plan models
├── reporting/   # Future reporting components
├── reports/     # Generated reports (except .gitkeep, ignored)
├── tests/       # Offline planner tests
├── main.py      # Placeholder application entry point
├── .env.example
├── .gitignore
└── requirements.txt
```

## Development status

Milestone 3: AI planning, schema validation, provider abstraction, offline tests, and initial safety screening. The planner does not execute browser actions. Browser locator resolution, action execution, a test runner, and final report generation remain future work.
