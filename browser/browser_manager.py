"""Lifecycle and navigation management for a single Playwright Chromium page."""

from __future__ import annotations

from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from config.settings import Settings, load_settings
from models.test_result import ResultCategory


class BrowserExecutionError(RuntimeError):
    """Browser setup or navigation failed with a stable result category."""

    code = ResultCategory.EXECUTION_ERROR


def validate_navigation_url(url: str) -> str:
    """Accept only valid HTTP(S) or local file URLs."""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise BrowserExecutionError("EXECUTION_ERROR: invalid navigation URL") from exc

    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    if (
        parsed.scheme == "file"
        and not parsed.netloc
        and bool(parsed.path)
        and not parsed.query
        and not parsed.fragment
    ):
        return url
    raise BrowserExecutionError(
        "EXECUTION_ERROR: URL must use http, https, or a local file URL"
    )


class BrowserManager:
    """Owns one browser and one page; use as a context manager for cleanup."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.page: Page | None = None

    def __enter__(self) -> BrowserManager:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()

    def start(self) -> BrowserManager:
        if self.page is not None:
            return self
        try:
            self._playwright = sync_playwright().start()
            self.browser = self._playwright.chromium.launch(headless=not self.settings.headed)
            self.page = self.browser.new_page()
            self.page.set_default_navigation_timeout(self.settings.navigation_timeout_ms)
            self.page.set_default_timeout(self.settings.action_timeout_ms)
            return self
        except Exception as exc:
            self.close()
            raise BrowserExecutionError(
                f"EXECUTION_ERROR: Chromium launch failed ({type(exc).__name__})"
            ) from exc

    def navigate(self, url: str) -> Page:
        validated_url = validate_navigation_url(url)
        page = self._require_page()
        try:
            page.goto(
                validated_url,
                wait_until="load",
                timeout=self.settings.navigation_timeout_ms,
            )
        except PlaywrightError as exc:
            message = self.settings.redact(str(exc))
            raise BrowserExecutionError(
                f"EXECUTION_ERROR: navigation failed: {message}"
            ) from exc
        return page

    def _require_page(self) -> Page:
        if self.page is None:
            raise BrowserExecutionError("EXECUTION_ERROR: browser has not been started")
        return self.page

    def close(self) -> None:
        browser, self.browser = self.browser, None
        page, self.page = self.page, None
        playwright, self._playwright = self._playwright, None

        try:
            if page is not None:
                page.close()
        finally:
            try:
                if browser is not None:
                    browser.close()
            finally:
                if playwright is not None:
                    playwright.stop()

    def stop(self) -> None:
        """Stop the browser and Playwright runtime."""
        self.close()


__all__ = ["BrowserExecutionError", "BrowserManager", "validate_navigation_url"]
