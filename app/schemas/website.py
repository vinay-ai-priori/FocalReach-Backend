from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.website_analysis import AnalysisStatus


class WebsiteAnalyzeRequest(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    force_refresh: bool = False


class CrawledPage(BaseModel):
    url: str
    title: str | None = None
    chars: int = 0


class WebsiteAnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    url: str
    domain: str
    status: AnalysisStatus
    error_message: str | None = None
    page_title: str | None = None
    meta_description: str | None = None
    crawled_pages: list | None = None
    used_playwright: bool = False
    scrape_engine: str | None = None
    created_at: datetime
    updated_at: datetime
    cached: bool = False
