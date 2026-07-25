from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable, Optional

from playwright.sync_api import Error as PlaywrightError, Locator, Page


def first_visible(locator: Locator, limit: int = 25) -> Optional[Locator]:
    try:
        for index in range(min(locator.count(), limit)):
            item = locator.nth(index)
            if item.is_visible():
                return item
    except PlaywrightError:
        return None
    return None


def locator_text(locator: Locator) -> str:
    try:
        return locator.inner_text(timeout=2_000)
    except PlaywrightError:
        try:
            return locator.text_content(timeout=2_000) or ""
        except PlaywrightError:
            return ""


def wait_for_visible(page: Page, selectors: Iterable[str], timeout_ms: int) -> Optional[Locator]:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.is_closed():
            return None
        for selector in selectors:
            item = first_visible(page.locator(selector))
            if item is not None:
                return item
        time.sleep(0.25)
    return None


def click_by_names(page: Page, names: Iterable[str], timeout_ms: int = 5_000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for name in names:
            pattern = re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)
            for role in ("button", "menuitem", "link"):
                try:
                    item = first_visible(page.get_by_role(role, name=pattern))
                    if item is not None:
                        item.click()
                        return True
                except PlaywrightError:
                    pass
            try:
                item = first_visible(page.get_by_text(pattern, exact=True))
                if item is not None:
                    item.click()
                    return True
            except PlaywrightError:
                pass
        time.sleep(0.2)
    return False


def body_text(page: Page, timeout_ms: int = 5_000) -> str:
    try:
        return page.locator("body").inner_text(timeout=timeout_ms)
    except PlaywrightError:
        return ""


def save_screenshot(page: Page | None, directory: Path, prefix: str) -> Path | None:
    if page is None or page.is_closed():
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = directory / f"{prefix}_{stamp}.png"
        page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:
        return None
