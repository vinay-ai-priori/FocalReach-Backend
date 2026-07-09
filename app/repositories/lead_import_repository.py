from app.models.lead_import import LeadImport
from app.repositories.base import BaseRepository


class LeadImportRepository(BaseRepository[LeadImport]):
    model = LeadImport
