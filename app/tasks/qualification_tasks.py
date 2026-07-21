from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.lead_import import ImportStatus
from app.repositories.lead_import_repository import LeadImportRepository
from app.services.campaign_service import active_icp_of
from app.services.qualification_service import qualify_import, reactivate_rejected

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="qualification.run")
def qualify_import_task(lead_import_id: int) -> dict:
    db = SessionLocal()
    try:
        import_repo = LeadImportRepository(db)
        lead_import = import_repo.get(lead_import_id)
        if not lead_import:
            return {"error": "import not found"}
        icp = active_icp_of(lead_import.campaign)
        import_repo.update(lead_import, status=ImportStatus.QUALIFYING)
        counts = qualify_import(db, lead_import, icp)
        logger.info("Qualification for import %s: %s", lead_import_id, counts)
        return counts
    finally:
        db.close()


@celery_app.task(name="qualification.reactivate_rejected")
def reactivate_rejected_task(lead_import_id: int, company_ids: list[int]) -> dict:
    """Enrich + score gate-rejected companies and flip them to REACTIVATED (user
    override). Their leads then flow to prioritization on the next scoring run."""
    db = SessionLocal()
    try:
        import_repo = LeadImportRepository(db)
        lead_import = import_repo.get(lead_import_id)
        if not lead_import:
            return {"error": "import not found"}
        icp = active_icp_of(lead_import.campaign)
        counts = reactivate_rejected(db, lead_import, icp, company_ids)
        logger.info("Reactivated rejected companies for import %s: %s", lead_import_id, counts)
        return counts
    finally:
        db.close()
