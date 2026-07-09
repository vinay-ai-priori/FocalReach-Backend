from pydantic import BaseModel, ConfigDict

from app.models.crm_connection import CRMProvider


class CRMProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: CRMProvider
    display_name: str
    is_connected: bool
    capabilities: list[str] = []


class CRMConnectRequest(BaseModel):
    config: dict = {}
