from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.repositories.website_repository import WebsiteAnalysisRepository
from app.services.company_intelligence_service import generate_company_intelligence

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="company_intelligence.generate")
def generate_intelligence_task(analysis_id: int) -> dict:
    db = SessionLocal()
    try:
        analysis = WebsiteAnalysisRepository(db).get(analysis_id)
        if not analysis:
            return {"error": "analysis not found"}
        intelligence = generate_company_intelligence(db, analysis)
        return {"company_intelligence_id": intelligence.id}
    finally:
        db.close()
