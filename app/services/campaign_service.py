"""Campaign aggregate: derives the flow stage and serializes to the API shape.

v2 schema: icps and lead_imports point UP to the campaign. Helpers below resolve the
campaign's active ICP and permanent (PRIMARY) import from those child collections."""

from app.models.campaign import Campaign
from app.models.icp import ICP
from app.models.lead_import import ImportKind, ImportStatus, LeadImport
from app.models.website_analysis import AnalysisStatus
from app.schemas.campaign import CampaignOut

# Ordered stages the frontend step sidebar mirrors.
STAGE_WEBSITE = "website"
STAGE_INTELLIGENCE = "intelligence"
STAGE_ICP = "icp"
STAGE_UPLOAD = "upload"
STAGE_QUALIFICATION = "qualification"
STAGE_PRIORITIZATION = "prioritization"
STAGE_OUTREACH = "outreach"


def active_icp_of(campaign: Campaign) -> ICP | None:
    return next((i for i in campaign.icps if i.is_active), None)


def primary_import_of(campaign: Campaign) -> LeadImport | None:
    return next((li for li in campaign.lead_imports if li.kind == ImportKind.PRIMARY), None)


def derive_stage(campaign: Campaign) -> str:
    """Where the campaign currently is, from the artifacts it has accumulated."""
    li = primary_import_of(campaign)
    if li:
        status = li.status
        if status in (ImportStatus.SCORED,):
            return STAGE_OUTREACH
        if status in (ImportStatus.SCORING, ImportStatus.QUALIFIED):
            return STAGE_PRIORITIZATION
        if status in (ImportStatus.QUALIFYING, ImportStatus.IMPORTED):
            return STAGE_QUALIFICATION
        return STAGE_UPLOAD  # MAPPING
    if active_icp_of(campaign):
        return STAGE_ICP
    if campaign.company_intelligence_id:
        return STAGE_INTELLIGENCE
    if campaign.website_analysis and campaign.website_analysis.status == AnalysisStatus.COMPLETED:
        return STAGE_INTELLIGENCE
    return STAGE_WEBSITE


def to_out(campaign: Campaign) -> CampaignOut:
    from app.services.csv.reupload_service import icp_fingerprint

    icp = active_icp_of(campaign)
    lead_import = primary_import_of(campaign)

    out = CampaignOut.model_validate(campaign)
    out.stage = derive_stage(campaign)
    out.analysis_status = campaign.website_analysis.status.value if campaign.website_analysis else None
    out.import_status = lead_import.status.value if lead_import else None
    out.website_analysis_public_id = campaign.website_analysis.public_id if campaign.website_analysis else None
    out.company_intelligence_public_id = campaign.company_intelligence.public_id if campaign.company_intelligence else None
    out.icp_public_id = icp.public_id if icp else None
    out.lead_import_public_id = lead_import.public_id if lead_import else None
    out.results_stale = bool(
        icp
        and lead_import
        and lead_import.icp_snapshot_hash
        and lead_import.icp_snapshot_hash != icp_fingerprint(icp)
    )
    return out
