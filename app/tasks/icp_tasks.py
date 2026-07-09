from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.repositories.company_intelligence_repository import CompanyIntelligenceRepository
from app.services.icp_service import generate_icp

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="icp.generate")
def generate_icp_task(company_intelligence_id: int) -> dict:
    db = SessionLocal()
    try:
        intelligence = CompanyIntelligenceRepository(db).get(company_intelligence_id)
        if not intelligence:
            return {"error": "company intelligence not found"}
        icp = generate_icp(db, intelligence)
        return {"icp_id": icp.id}
    finally:
        db.close()
