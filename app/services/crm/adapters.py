from app.models.crm_connection import CRMProvider
from app.services.crm.base import CRMAdapter, StubAdapter


class SalesforceAdapter(StubAdapter):
    provider = CRMProvider.SALESFORCE
    display_name = "Salesforce"


class HubSpotAdapter(StubAdapter):
    provider = CRMProvider.HUBSPOT
    display_name = "HubSpot"


class ZohoAdapter(StubAdapter):
    provider = CRMProvider.ZOHO
    display_name = "Zoho CRM"


class DynamicsAdapter(StubAdapter):
    provider = CRMProvider.DYNAMICS
    display_name = "Microsoft Dynamics 365"


class PipedriveAdapter(StubAdapter):
    provider = CRMProvider.PIPEDRIVE
    display_name = "Pipedrive"


ADAPTERS: dict[CRMProvider, CRMAdapter] = {
    adapter.provider: adapter()
    for adapter in (SalesforceAdapter, HubSpotAdapter, ZohoAdapter, DynamicsAdapter, PipedriveAdapter)
}


def get_adapter(provider: CRMProvider) -> CRMAdapter:
    return ADAPTERS[provider]
