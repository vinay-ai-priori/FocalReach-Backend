from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.lead_import import ImportStatus
from app.repositories.icp_repository import ICPRepository
from app.repositories.lead_import_repository import LeadImportRepository
from app.services.scoring_service import score_import

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="scoring.run")
def score_import_task(lead_import_id: int) -> dict:
    db = SessionLocal()
    try:
        import_repo = LeadImportRepository(db)
        lead_import = import_repo.get(lead_import_id)
        if not lead_import:
            return {"error": "import not found"}
        icp = ICPRepository(db).get(lead_import.icp_id)
        import_repo.update(lead_import, status=ImportStatus.SCORING)
        counts = score_import(db, lead_import, icp)
        logger.info("Scoring for import %s: %s", lead_import_id, counts)
        return counts
    finally:
        db.close()
