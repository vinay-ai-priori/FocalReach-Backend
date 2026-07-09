"""Modular CRM integration layer — framework only for MVP.

Each provider implements CRMAdapter. Real API calls are Phase 2; today the adapters
declare capabilities and return NotImplemented-style responses so the rest of the
app can code against a stable interface."""

from abc import ABC, abstractmethod

from app.models.crm_connection import CRMProvider


class CRMAdapter(ABC):
    provider: CRMProvider
    display_name: str
    capabilities: list[str] = ["push_leads", "push_companies", "sync_status"]

    @abstractmethod
    def test_connection(self, config: dict) -> bool: ...

    @abstractmethod
    def push_leads(self, config: dict, leads: list[dict]) -> dict: ...


class StubAdapter(CRMAdapter):
    """Shared MVP behaviour: connection always 'succeeds', pushes are acknowledged but queued."""

    def test_connection(self, config: dict) -> bool:
        return True

    def push_leads(self, config: dict, leads: list[dict]) -> dict:
        return {"accepted": len(leads), "status": "queued", "note": "CRM sync is framework-only in the MVP."}
