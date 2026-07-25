from __future__ import annotations

import sys

from .models import ApprovalDecision, ApprovalRequest


class TerminalApprovalHandler:
    """Yazma ve komut çağrılarını terminalde açık kullanıcı onayına bağlar."""

    def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        if not sys.stdin.isatty():
            return ApprovalDecision(approved=False)
        print("\n" + "=" * 72)
        print(f"[ARAÇ ONAYI] {request.definition.title}")
        print(f"Risk: {request.definition.risk.value}")
        print(f"İşlem: {request.summary}")
        print("1) Bu kez onayla")
        print("2) Bu sohbet boyunca bu aracı onayla")
        print("3) Reddet")
        while True:
            answer = input("Seçim [1/2/3]: ").strip().casefold()
            if answer in {"1", "e", "evet", "y", "yes"}:
                return ApprovalDecision(approved=True)
            if answer == "2":
                return ApprovalDecision(approved=True, remember_for_session=True)
            if answer in {"3", "h", "hayır", "hayir", "n", "no", ""}:
                return ApprovalDecision(approved=False)
            print("Geçersiz seçim.")
