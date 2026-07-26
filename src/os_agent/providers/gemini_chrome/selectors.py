from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_ALLOWED_CHAIN_NAMES = (
    "input",
    "response",
    "send_button",
    "stop_button",
    "model_button",
)
_ALLOWED_MODEL_ROLES = {"menuitem", "option", "button", "radio", "tab"}
_PLAYWRIGHT_ONLY_FRAGMENTS = (
    ":has-text(",
    ":text(",
    ":text-is(",
    "text=",
    "xpath=",
    ">>",
)


def _deduplicate(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _configured_strings(payload: Mapping[str, Any], key: str, default: Sequence[str]) -> tuple[str, ...]:
    raw = payload.get(key, default)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{key} liste olmalı")
    values = _deduplicate([str(item) for item in raw])
    if not values:
        raise ValueError(f"{key} boş olamaz")
    return values


@dataclass(frozen=True, slots=True)
class SelectorChain:
    name: str
    candidates: tuple[str, ...]
    critical: bool = False
    dynamic: bool = False

    def ordered(self, preferred: str | None = None) -> tuple[str, ...]:
        if preferred and preferred in self.candidates:
            return (preferred, *(item for item in self.candidates if item != preferred))
        return self.candidates

    def validate_static(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.name not in _ALLOWED_CHAIN_NAMES:
            errors.append(f"Bilinmeyen selector zinciri: {self.name}")
        if not self.candidates:
            errors.append(f"{self.name}: en az bir selector gerekli")
        if self.critical and len(self.candidates) < 3:
            errors.append(f"{self.name}: kritik zincirde en az üç fallback selector gerekli")
        if len(set(self.candidates)) != len(self.candidates):
            errors.append(f"{self.name}: yinelenen selector bulundu")
        for selector in self.candidates:
            if len(selector) > 512:
                errors.append(f"{self.name}: selector çok uzun")
            if any(ord(char) < 32 for char in selector):
                errors.append(f"{self.name}: kontrol karakteri içeriyor: {selector!r}")
            folded = selector.casefold()
            if any(fragment in folded for fragment in _PLAYWRIGHT_ONLY_FRAGMENTS):
                errors.append(
                    f"{self.name}: native querySelector ile doğrulanamayan Playwright selector kullanıyor: {selector}"
                )
            if selector.count("[") != selector.count("]"):
                errors.append(f"{self.name}: köşeli parantez dengesi bozuk: {selector}")
            if selector.count("(") != selector.count(")"):
                errors.append(f"{self.name}: parantez dengesi bozuk: {selector}")
        return tuple(errors)


_DEFAULT_CHAINS = (
    SelectorChain(
        "input",
        (
            '[data-test-id="input-area"] rich-textarea div[contenteditable="true"]',
            'rich-textarea div[contenteditable="true"]',
            'rich-textarea [contenteditable="true"]',
            'div.ql-editor[contenteditable="true"]',
            '[contenteditable="true"][role="textbox"]',
            'textarea[aria-label*="prompt" i]',
            'textarea[placeholder*="Gemini" i]',
        ),
        critical=True,
    ),
    SelectorChain(
        "response",
        (
            "model-response .message-content",
            "model-response",
            '[data-message-author-role="model"]',
            '[data-message-author-role="assistant"]',
        ),
        dynamic=True,
    ),
    SelectorChain(
        "send_button",
        (
            'button[data-test-id="send-button"]',
            'button[aria-label*="Send" i]',
            'button[aria-label*="Gönder" i]',
        ),
        dynamic=True,
    ),
    SelectorChain(
        "stop_button",
        (
            'button[data-test-id="stop-button"]',
            'button[aria-label*="Stop response" i]',
            'button[aria-label*="Stop generating" i]',
            'button[aria-label*="Yanıtı durdur" i]',
            'button[aria-label*="Oluşturmayı durdur" i]',
        ),
        dynamic=True,
    ),
    SelectorChain(
        "model_button",
        (
            'button[data-test-id="bard-mode-menu-button"]',
            '.model-picker-container button',
            'button[data-test-id*="model" i]',
            'button[aria-label*="model" i]',
            'button[aria-haspopup="menu"][aria-label*="Gemini" i]',
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SelectorRegistry:
    chains: tuple[SelectorChain, ...]

    @classmethod
    def defaults(cls) -> "SelectorRegistry":
        return cls(_DEFAULT_CHAINS)

    @classmethod
    def from_config(cls, payload: Any) -> "SelectorRegistry":
        if payload in (None, ""):
            return cls.defaults()
        if not isinstance(payload, Mapping):
            raise ValueError("selector_contract nesne olmalı")

        replace_defaults = bool(payload.get("replace_defaults", False))
        raw_chains = payload.get("chains", payload)
        if not isinstance(raw_chains, Mapping):
            raise ValueError("selector_contract.chains nesne olmalı")

        unknown = set(raw_chains) - set(_ALLOWED_CHAIN_NAMES) - {"replace_defaults"}
        if unknown:
            raise ValueError(f"Bilinmeyen selector zincirleri: {', '.join(sorted(unknown))}")

        result: list[SelectorChain] = []
        for default in _DEFAULT_CHAINS:
            raw = raw_chains.get(default.name)
            critical = default.critical
            dynamic = default.dynamic
            configured: tuple[str, ...] = ()
            replace_chain = replace_defaults

            if raw is not None:
                if isinstance(raw, Mapping):
                    raw_candidates = raw.get("candidates", ())
                    critical = bool(raw.get("critical", critical))
                    dynamic = bool(raw.get("dynamic", dynamic))
                    replace_chain = bool(raw.get("replace_defaults", replace_chain))
                else:
                    raw_candidates = raw
                if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
                    raise ValueError(f"{default.name}.candidates liste olmalı")
                configured = _deduplicate([str(item) for item in raw_candidates])

            candidates = configured if replace_chain else _deduplicate((*configured, *default.candidates))
            chain = SelectorChain(default.name, candidates, critical=critical, dynamic=dynamic)
            result.append(chain)

        registry = cls(tuple(result))
        errors = registry.validate_static()
        if errors:
            raise ValueError("; ".join(errors))
        return registry

    def chain(self, name: str) -> SelectorChain:
        for chain in self.chains:
            if chain.name == name:
                return chain
        raise KeyError(name)

    def candidates(self, name: str) -> tuple[str, ...]:
        return self.chain(name).candidates

    def validate_static(self) -> tuple[str, ...]:
        errors: list[str] = []
        names = [chain.name for chain in self.chains]
        if len(set(names)) != len(names):
            errors.append("Selector zinciri adları yinelenemez")
        if set(names) != set(_ALLOWED_CHAIN_NAMES):
            errors.append("Selector registry bütün zorunlu zincirleri içermiyor")
        for chain in self.chains:
            errors.extend(chain.validate_static())
        return tuple(errors)

    def probe_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "name": chain.name,
                "selectors": list(chain.candidates),
                "critical": chain.critical,
                "dynamic": chain.dynamic,
            }
            for chain in self.chains
        ]

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.probe_payload(), ensure_ascii=True, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelSelectionPolicy:
    default_names: tuple[str, ...]
    option_roles: tuple[str, ...]
    button_names: tuple[str, ...]
    aliases: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def from_config(cls, payload: Any) -> "ModelSelectionPolicy":
        if payload in (None, ""):
            payload = {}
        if not isinstance(payload, Mapping):
            raise ValueError("model_ui nesne olmalı")

        default_names = _configured_strings(payload, "default_names", ("default",))
        option_roles = _configured_strings(payload, "option_roles", ("menuitem", "option", "button"))
        button_names = _configured_strings(payload, "button_names", ("model",))
        raw_aliases = payload.get("aliases", {})
        if not isinstance(raw_aliases, Mapping):
            raise ValueError("model_ui.aliases nesne olmalı")

        aliases: list[tuple[str, tuple[str, ...]]] = []
        for canonical, values in raw_aliases.items():
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise ValueError(f"Model alias listesi geçersiz: {canonical}")
            canonical_text = str(canonical).strip()
            if not canonical_text:
                raise ValueError("Boş model alias anahtarı kullanılamaz")
            aliases.append((canonical_text, _deduplicate([str(item) for item in values])))

        unsupported_roles = set(option_roles) - _ALLOWED_MODEL_ROLES
        if unsupported_roles:
            raise ValueError(
                "model_ui.option_roles desteklenmeyen roller içeriyor: "
                + ", ".join(sorted(unsupported_roles))
            )
        return cls(default_names, option_roles, button_names, tuple(aliases))

    def is_default(self, target: str) -> bool:
        folded = _normalize_label(target)
        return not folded or folded in {_normalize_label(item) for item in self.default_names}

    def labels_for(self, target: str) -> tuple[str, ...]:
        labels: list[str] = [target]
        folded = _normalize_label(target)
        for canonical, aliases in self.aliases:
            if _normalize_label(canonical) == folded:
                labels.extend(aliases)
                break
        return _deduplicate(labels)

    def all_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        for canonical, aliases in self.aliases:
            labels.append(canonical)
            labels.extend(aliases)
        return _deduplicate(labels)

    def matches(self, current: str, target: str) -> bool:
        current_folded = _normalize_label(current)
        if not current_folded:
            return False
        for label in self.labels_for(target):
            candidate = _normalize_label(label)
            if not candidate:
                continue
            if candidate == current_folded:
                return True
            pattern = rf"(?<!\w){re.escape(candidate)}(?!\w)"
            if re.search(pattern, current_folded, re.IGNORECASE):
                return True
        return False


@dataclass(frozen=True, slots=True)
class UiLabelPolicy:
    input: tuple[str, ...]
    new_chat: tuple[str, ...]
    send: tuple[str, ...]
    stop: tuple[str, ...]
    retry: tuple[str, ...]

    @classmethod
    def from_config(cls, payload: Any) -> "UiLabelPolicy":
        if payload in (None, ""):
            payload = {}
        if not isinstance(payload, Mapping):
            raise ValueError("ui_labels nesne olmalı")
        return cls(
            input=_configured_strings(
                payload,
                "input",
                ("Enter a prompt here", "Ask Gemini", "Gemini'a sorun", "İstem girin"),
            ),
            new_chat=_configured_strings(payload, "new_chat", ("New chat", "Yeni sohbet")),
            send=_configured_strings(payload, "send", ("Send", "Gönder")),
            stop=_configured_strings(
                payload,
                "stop",
                ("Stop response", "Stop generating", "Yanıtı durdur", "Oluşturmayı durdur"),
            ),
            retry=_configured_strings(payload, "retry", ("Try again", "Tekrar dene", "Yeniden dene")),
        )


@dataclass(frozen=True, slots=True)
class SelectorHealthPolicy:
    enabled: bool
    interval_seconds: int
    read_interval_seconds: int
    failure_threshold: int
    report_interval_seconds: int

    @classmethod
    def from_config(cls, payload: Any) -> "SelectorHealthPolicy":
        if payload in (None, ""):
            payload = {}
        if not isinstance(payload, Mapping):
            raise ValueError("selector_health nesne olmalı")
        return cls(
            enabled=bool(payload.get("enabled", True)),
            interval_seconds=max(30, int(payload.get("interval_seconds", 60))),
            read_interval_seconds=max(5, int(payload.get("read_interval_seconds", 15))),
            failure_threshold=max(1, int(payload.get("failure_threshold", 3))),
            report_interval_seconds=max(60, int(payload.get("report_interval_seconds", 300))),
        )


_DEFAULT_REGISTRY = SelectorRegistry.defaults()
INPUT_SELECTORS = _DEFAULT_REGISTRY.candidates("input")
RESPONSE_SELECTORS = _DEFAULT_REGISTRY.candidates("response")
SEND_BUTTON_SELECTORS = _DEFAULT_REGISTRY.candidates("send_button")
STOP_BUTTON_SELECTORS = _DEFAULT_REGISTRY.candidates("stop_button")
MODEL_BUTTON_SELECTORS = _DEFAULT_REGISTRY.candidates("model_button")
NEW_CHAT_NAMES = ("New chat", "Yeni sohbet")
