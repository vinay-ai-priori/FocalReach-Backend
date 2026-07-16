from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    calcom,
    campaigns,
    company_intelligence,
    crm,
    icps,
    imports,
    leads,
    mailboxes,
    notifications,
    outreach,
    qualification,
    websites,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(campaigns.router)
api_router.include_router(websites.router)
api_router.include_router(company_intelligence.router)
api_router.include_router(icps.router)
api_router.include_router(imports.router)
api_router.include_router(qualification.router)
api_router.include_router(leads.router)
api_router.include_router(outreach.router)
api_router.include_router(notifications.router)
api_router.include_router(crm.router)
api_router.include_router(mailboxes.router)
api_router.include_router(calcom.router)
