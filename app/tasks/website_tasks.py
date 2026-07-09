from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.website_analysis import AnalysisStatus
from app.repositories.website_repository import WebsiteAnalysisRepository
from app.services.company_intelligence_service import generate_company_intelligence
from app.services.website.cache import get_cached_content, set_cached_content
from app.services.website.crawler import crawl_website

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="website.analyze", bind=True, max_retries=1)
def analyze_website_task(self, analysis_id: int) -> dict:
    """Scrape the website (cache-aware), then chain into company intelligence generation."""
    db = SessionLocal()
    try:
        repo = WebsiteAnalysisRepository(db)
        analysis = repo.get(analysis_id)
        if not analysis:
            return {"error": "analysis not found"}

        cache_key = f"{analysis.organization_id or 'global'}:{analysis.domain}"
        try:
            cached = get_cached_content(cache_key)
            if cached and analysis.extracted_content:
                logger.info("Domain %s already analyzed; skipping scrape", analysis.domain)
            else:
                repo.update(analysis, status=AnalysisStatus.SCRAPING)
                result = crawl_website(analysis.url)
                repo.update(
                    analysis,
                    status=AnalysisStatus.EXTRACTING,
                    extracted_content=result.content,
                    page_title=result.page_title,
                    meta_description=result.meta_description,
                    crawled_pages=result.pages,
                    used_playwright=result.used_playwright,
                    scrape_engine=result.engine,
                )
                set_cached_content(cache_key, {"content": result.content[:20000]})

            repo.update(analysis, status=AnalysisStatus.GENERATING_INTELLIGENCE)
            generate_company_intelligence(db, analysis)
            repo.update(analysis, status=AnalysisStatus.COMPLETED, error_message=None)
            return {"analysis_id": analysis.id, "status": "completed"}
        except Exception as exc:
            logger.exception("Website analysis %s failed", analysis_id)
            repo.update(analysis, status=AnalysisStatus.FAILED, error_message=str(exc)[:1000])
            return {"analysis_id": analysis_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()
