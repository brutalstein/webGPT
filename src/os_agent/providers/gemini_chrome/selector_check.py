from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .selectors import (
    ModelSelectionPolicy,
    SelectorHealthPolicy,
    SelectorRegistry,
    UiLabelPolicy,
)

_FIXTURE = """
<!doctype html>
<html lang="tr">
<body>
  <div data-test-id="input-area">
    <rich-textarea><div contenteditable="true" role="textbox" aria-label="İstem girin"></div></rich-textarea>
  </div>
  <model-response><div class="message-content">örnek yanıt</div></model-response>
  <button data-test-id="send-button" aria-label="Gönder">Gönder</button>
  <button data-test-id="stop-button" aria-label="Yanıtı durdur">Durdur</button>
  <div class="model-picker-container">
    <button data-test-id="bard-mode-menu-button" aria-label="Gemini model">3.1 Pro</button>
  </div>
</body>
</html>
"""


def _load_provider_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("config.json providers nesnesi içermiyor")
    gemini = providers.get("gemini")
    if not isinstance(gemini, dict):
        raise ValueError("config.json Gemini provider ayarı içermiyor")
    return gemini


def _browser_smoke(registry: SelectorRegistry) -> dict[str, Any]:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    groups: dict[str, Any] = {}
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(_FIXTURE, wait_until="domcontentloaded")
            for chain in registry.chains:
                candidates: list[dict[str, Any]] = []
                matched = False
                for selector in chain.candidates:
                    try:
                        count = page.locator(selector).count()
                        candidates.append({"selector": selector, "valid": True, "count": count})
                        matched = matched or count > 0
                    except PlaywrightError as exc:
                        candidates.append(
                            {
                                "selector": selector,
                                "valid": False,
                                "count": 0,
                                "error": str(exc)[:300],
                            }
                        )
                        errors.append(f"{chain.name}: geçersiz selector: {selector}")
                if not matched:
                    errors.append(f"{chain.name}: doğrulama fixture'ında hiçbir fallback eşleşmedi")
                groups[chain.name] = {"matched": matched, "candidates": candidates}
        finally:
            browser.close()
    return {"ok": not errors, "errors": errors, "groups": groups}


def build_report(config_path: Path, *, browser_smoke: bool) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        provider = _load_provider_config(config_path)
        registry = SelectorRegistry.from_config(provider.get("selector_contract", {}))
        model_policy = ModelSelectionPolicy.from_config(provider.get("model_ui", {}))
        ui_labels = UiLabelPolicy.from_config(provider.get("ui_labels", {}))
        health = SelectorHealthPolicy.from_config(provider.get("selector_health", {}))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [], "config": str(config_path)}

    errors.extend(registry.validate_static())
    preferred_model = str(provider.get("preferred_model", "")).strip()
    aliases = model_policy.labels_for(preferred_model)
    if not model_policy.is_default(preferred_model) and len(aliases) < 2:
        warnings.append(
            "Tercih edilen model için alternatif UI etiketi yok; Google metin değişikliğinde seçim kırılabilir."
        )
    if not ui_labels.input:
        errors.append("ui_labels.input en az bir erişilebilir etiket içermeli")

    report: dict[str, Any] = {
        "ok": not errors,
        "config": str(config_path),
        "contract_hash": registry.fingerprint,
        "errors": list(errors),
        "warnings": warnings,
        "preferred_model": preferred_model,
        "preferred_model_labels": list(aliases),
        "chains": {
            chain.name: {
                "critical": chain.critical,
                "dynamic": chain.dynamic,
                "fallbacks": len(chain.candidates),
                "selectors": list(chain.candidates),
            }
            for chain in registry.chains
        },
        "selector_health": {
            "enabled": health.enabled,
            "interval_seconds": health.interval_seconds,
            "read_interval_seconds": health.read_interval_seconds,
            "failure_threshold": health.failure_threshold,
            "report_interval_seconds": health.report_interval_seconds,
        },
    }

    if browser_smoke and not errors:
        try:
            smoke = _browser_smoke(registry)
        except Exception as exc:
            smoke = {"ok": False, "errors": [f"Browser smoke başlatılamadı: {exc}"], "groups": {}}
        report["browser_smoke"] = smoke
        if not smoke.get("ok"):
            report["ok"] = False
            report["errors"].extend(smoke.get("errors", []))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini selector sözleşmesini doğrular")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--browser-smoke", action="store_true")
    args = parser.parse_args()

    report = build_report(args.config.resolve(), browser_smoke=args.browser_smoke)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
