# CloudQA Task 2

An initial project scaffold for an AI-assisted, natural-language browser test automation project. Application features are not implemented yet.

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

When Gemini integration is introduced in a later milestone, copy `.env.example` to `.env` and set `GEMINI_API_KEY` locally. Never commit `.env` or share its contents. No API key is needed for the current scaffold or browser verification.

## Project structure

```text
CloudQA-Task2/
├── ai/          # Future AI components
├── browser/     # Future browser automation components
├── config/      # Configuration package
├── engine/      # Future test execution components
├── fixtures/    # Test fixtures
├── models/      # Data models
├── reporting/   # Future reporting components
├── reports/     # Generated reports (except .gitkeep, ignored)
├── tests/       # Tests
├── main.py      # Placeholder entry point
├── .env.example
├── .gitignore
└── requirements.txt
```

## Development status

Milestone 2: environment verification and project scaffolding. The AI test planner, Gemini calls, natural-language interpretation, locator engine, browser action executor, test runner, and final report generation are not implemented.
