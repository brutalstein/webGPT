from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, Page

from .selectors import SelectorHealthPolicy, SelectorRegistry

HealthEventHandler = Callable[[str, dict[str, Any]], None]
_HEALTH_KEY = "__osSelectorHealthV1"

_INSTALL_FUNCTION = r"""
(payload) => {
  const key = payload.key;
  const previous = window[key];
  if (previous && previous.timerId) window.clearInterval(previous.timerId);

  const collectRoots = () => {
    const roots = [document];
    for (let index = 0; index < roots.length; index += 1) {
      const root = roots[index];
      for (const node of root.querySelectorAll('*')) {
        if (node.shadowRoot) roots.push(node.shadowRoot);
      }
    }
    return roots;
  };

  const queryAllDeep = (roots, selector) => {
    const result = [];
    const seen = new Set();
    for (const root of roots) {
      for (const node of root.querySelectorAll(selector)) {
        if (!seen.has(node)) {
          seen.add(node);
          result.push(node);
        }
      }
    }
    return result;
  };

  const visibleCount = (roots, selector) => {
    const allNodes = queryAllDeep(roots, selector);
    const nodes = allNodes.slice(0, 25);
    let visible = 0;
    for (const node of nodes) {
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      if (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number(style.opacity || 1) !== 0 &&
        rect.width > 0 &&
        rect.height > 0
      ) visible += 1;
    }
    return { total: allNodes.length, visible };
  };

  const state = {
    version: 1,
    contractHash: payload.contractHash,
    timerId: null,
    snapshot: null,
  };

  const probe = () => {
    const groups = {};
    const roots = collectRoots();
    for (const chain of payload.chains) {
      const candidates = [];
      let firstMatch = null;
      for (let index = 0; index < chain.selectors.length; index += 1) {
        const selector = chain.selectors[index];
        try {
          const counts = visibleCount(roots, selector);
          const entry = { selector, index, ...counts, valid: true };
          candidates.push(entry);
          if (firstMatch === null && counts.visible > 0) firstMatch = entry;
        } catch (error) {
          candidates.push({
            selector,
            index,
            total: 0,
            visible: 0,
            valid: false,
            error: String(error && error.message ? error.message : error).slice(0, 240),
          });
        }
      }
      groups[chain.name] = {
        critical: Boolean(chain.critical),
        dynamic: Boolean(chain.dynamic),
        present: firstMatch !== null,
        firstMatch,
        candidates,
      };
    }
    const path = location.pathname.startsWith('/app') ? '/app' : location.pathname;
    state.snapshot = {
      version: 1,
      contractHash: payload.contractHash,
      sampledAt: new Date().toISOString(),
      page: `${location.origin}${path}`,
      groups,
    };
    return state.snapshot;
  };

  state.timerId = window.setInterval(probe, payload.intervalMs);
  window[key] = state;
  return probe();
}
"""

_READ_SCRIPT = f"() => window.{_HEALTH_KEY} ? window.{_HEALTH_KEY}.snapshot : null"


def critical_failures(snapshot: dict[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(snapshot, dict):
        return ("monitor_unavailable",)
    groups = snapshot.get("groups")
    if not isinstance(groups, dict):
        return ("monitor_malformed",)
    resolutions = snapshot.get("resolutions")
    resolved_groups = set(resolutions) if isinstance(resolutions, dict) else set()
    failures: list[str] = []
    for name, item in groups.items():
        if not isinstance(item, dict):
            continue
        if (
            bool(item.get("critical"))
            and not bool(item.get("present"))
            and str(name) not in resolved_groups
        ):
            failures.append(str(name))
    return tuple(sorted(failures))


class SelectorHealthMonitor:
    """Gemini DOM selector sözleşmesini düşük maliyetle izler.

    Playwright'ın thread-affinity kuralını bozmaz. Periyodik DOM taraması sayfanın
    kendi JavaScript zamanlayıcısında çalışır; Python yalnız provider thread'i
    güvenli bir noktaya geldiğinde snapshot'ı okur ve raporlar.
    """

    def __init__(
        self,
        page: Page,
        registry: SelectorRegistry,
        policy: SelectorHealthPolicy,
        report_dir: Path,
        emit: HealthEventHandler,
    ):
        self.page = page
        self.registry = registry
        self.policy = policy
        self.report_dir = report_dir
        self.emit = emit
        self.report_path = report_dir / "selector-health.json"
        self._init_script_added = False
        self._next_read_at = 0.0
        self._last_report_at = 0.0
        self._last_digest = ""
        self._last_snapshot: dict[str, Any] | None = None
        self._failures: dict[str, int] = {}
        self._drift_announced: set[str] = set()
        self._resolutions: dict[str, dict[str, Any]] = {}
        self._operation_failures: dict[str, dict[str, Any]] = {}
        self._last_first_matches: dict[str, str] = {}
        self._load_previous_baseline()


    def _load_previous_baseline(self) -> None:
        try:
            payload = json.loads(self.report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("contractHash") != self.registry.fingerprint:
            return
        groups = payload.get("groups")
        if not isinstance(groups, dict):
            return
        for name, item in groups.items():
            if not isinstance(item, dict):
                continue
            first = item.get("firstMatch")
            if isinstance(first, dict) and isinstance(first.get("selector"), str):
                self._last_first_matches[str(name)] = str(first["selector"])

    def _payload(self) -> dict[str, Any]:
        return {
            "key": _HEALTH_KEY,
            "contractHash": self.registry.fingerprint,
            "chains": self.registry.probe_payload(),
            "intervalMs": self.policy.interval_seconds * 1_000,
        }

    def install(self, *, force: bool = False) -> bool:
        if not self.policy.enabled or self.page.is_closed():
            return False
        payload = self._payload()
        try:
            if not self._init_script_added:
                script = f"({_INSTALL_FUNCTION})({json.dumps(payload, ensure_ascii=True)});"
                self.page.add_init_script(script=script)
                self._init_script_added = True
            if force or self.page.evaluate(_READ_SCRIPT) is None:
                self.page.evaluate(_INSTALL_FUNCTION, payload)
            return True
        except PlaywrightError:
            return False

    def observe(self, group: str, selector: str, *, strategy: str, index: int | None = None) -> None:
        previous = self._resolutions.get(group, {}).get("selector")
        if previous and previous != selector:
            self.emit(
                "selector.fallback_changed",
                {
                    "group": group,
                    "previous": previous,
                    "current": selector,
                    "strategy": strategy,
                    "report": str(self.report_path),
                },
            )
        self._resolutions[group] = {
            "selector": selector,
            "strategy": strategy,
            "index": index,
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._operation_failures.pop(group, None)
        self._failures[group] = 0
        self._drift_announced.discard(group)

    def record_failure(self, group: str, reason: str) -> None:
        previous = self._operation_failures.get(group)
        now = time.monotonic()
        if previous and now - float(previous.get("monotonic", 0.0)) < 5.0:
            return
        self._operation_failures[group] = {
            "reason": str(reason)[:500],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "monotonic": now,
        }
        self._failures[group] = self._failures.get(group, 0) + 1
        if self._failures[group] >= self.policy.failure_threshold and group not in self._drift_announced:
            self._drift_announced.add(group)
            self.emit(
                "selector.drift",
                {
                    "group": group,
                    "reason": str(reason)[:500],
                    "failures": self._failures[group],
                    "report": str(self.report_path),
                },
            )

    @staticmethod
    def _page_identity(url: str) -> str:
        try:
            parsed = urlsplit(url)
            path = "/app" if parsed.path.startswith("/app") else parsed.path
            return f"{parsed.scheme}://{parsed.netloc}{path}"
        except ValueError:
            return ""

    def maybe_probe(self, reason: str, *, force: bool = False) -> dict[str, Any] | None:
        if not self.policy.enabled or self.page.is_closed():
            return self._last_snapshot
        now = time.monotonic()
        if not force and now < self._next_read_at:
            return self._last_snapshot
        self._next_read_at = now + self.policy.read_interval_seconds

        if not self.install(force=False):
            self.record_failure("monitor", "Selector health script kurulamadı")
            return self._last_snapshot
        try:
            snapshot = self.page.evaluate(_READ_SCRIPT)
            if not isinstance(snapshot, dict):
                if not self.install(force=True):
                    return self._last_snapshot
                snapshot = self.page.evaluate(_READ_SCRIPT)
        except PlaywrightError:
            self.record_failure("monitor", "Selector health snapshot okunamadı")
            return self._last_snapshot
        if not isinstance(snapshot, dict):
            return self._last_snapshot

        snapshot = dict(snapshot)
        snapshot["page"] = self._page_identity(str(snapshot.get("page") or self.page.url))
        snapshot["reason"] = reason
        snapshot["resolutions"] = dict(self._resolutions)
        snapshot["operation_failures"] = {
            key: {item_key: item_value for item_key, item_value in value.items() if item_key != "monotonic"}
            for key, value in self._operation_failures.items()
        }

        groups = snapshot.get("groups", {})
        if isinstance(groups, dict):
            for group, item in groups.items():
                if not isinstance(item, dict):
                    continue
                first = item.get("firstMatch")
                current = str(first.get("selector")) if isinstance(first, dict) and first.get("selector") else ""
                previous = self._last_first_matches.get(str(group), "")
                if previous and current and previous != current:
                    self.emit(
                        "selector.fallback_changed",
                        {
                            "group": str(group),
                            "previous": previous,
                            "current": current,
                            "strategy": "background-dom-probe",
                            "report": str(self.report_path),
                        },
                    )
                if current:
                    self._last_first_matches[str(group)] = current

        failures = critical_failures(snapshot)
        for group in failures:
            self._failures[group] = self._failures.get(group, 0) + 1
            if self._failures[group] >= self.policy.failure_threshold and group not in self._drift_announced:
                self._drift_announced.add(group)
                self.emit(
                    "selector.drift",
                    {
                        "group": group,
                        "failures": self._failures[group],
                        "report": str(self.report_path),
                    },
                )
        for chain in self.registry.chains:
            if chain.name not in failures and chain.name not in self._operation_failures:
                self._failures[chain.name] = 0
                self._drift_announced.discard(chain.name)

        snapshot["critical_failures"] = list(failures)
        snapshot["failure_counts"] = dict(self._failures)
        snapshot["state"] = "degraded" if failures or self._operation_failures else "healthy"
        self._last_snapshot = snapshot

        # sampledAt/reason her okumada değişir. Rapor yalnız anlamlı selector
        # durumu değiştiğinde veya rapor aralığı dolduğunda diske yazılır.
        digest_view = {
            "page": snapshot.get("page"),
            "contractHash": snapshot.get("contractHash"),
            "groups": {
                name: {
                    "present": item.get("present"),
                    "firstMatch": item.get("firstMatch"),
                    "invalid": [
                        candidate.get("selector")
                        for candidate in item.get("candidates", [])
                        if isinstance(candidate, dict) and not candidate.get("valid", True)
                    ],
                }
                for name, item in snapshot.get("groups", {}).items()
                if isinstance(item, dict)
            },
            "resolutions": snapshot.get("resolutions"),
            "operation_failures": snapshot.get("operation_failures"),
            "critical_failures": snapshot.get("critical_failures"),
            "failure_counts": snapshot.get("failure_counts"),
            "state": snapshot.get("state"),
        }
        digest_payload = json.dumps(digest_view, ensure_ascii=True, sort_keys=True, default=str)
        digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        should_report = digest != self._last_digest or now - self._last_report_at >= self.policy.report_interval_seconds
        if should_report:
            self._last_digest = digest
            self._last_report_at = now
            self._write_report(snapshot)
            self.emit(
                "selector.health",
                {
                    "state": snapshot["state"],
                    "critical_failures": list(failures),
                    "contract_hash": self.registry.fingerprint,
                    "report": str(self.report_path),
                },
            )
        return snapshot

    def _write_report(self, snapshot: dict[str, Any]) -> None:
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.report_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
            os.replace(temporary, self.report_path)
        except OSError:
            pass

    def status(self) -> dict[str, Any]:
        if self._last_snapshot is not None:
            return dict(self._last_snapshot)
        return {
            "state": "waiting" if self.policy.enabled else "disabled",
            "contract_hash": self.registry.fingerprint,
            "report": str(self.report_path),
        }
