"""Campaign aggregate: derives the flow stage and serializes to the API shape."""

from app.models.campaign import Campaign
from app.models.lead_import import ImportStatus
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


def derive_stage(campaign: Campaign) -> str:
    """Where the campaign currently is, from the artifacts it has accumulated."""
    li = campaign.lead_import
    if li:
        status = li.status
        if status in (ImportStatus.SCORED,):
            return STAGE_OUTREACH
        if status in (ImportStatus.SCORING, ImportStatus.QUALIFIED):
            return STAGE_PRIORITIZATION
        if status in (ImportStatus.QUALIFYING, ImportStatus.IMPORTED):
            return STAGE_QUALIFICATION
        return STAGE_UPLOAD  # MAPPING
    if campaign.icp_id:
        return STAGE_ICP
    if campaign.company_intelligence_id:
        return STAGE_INTELLIGENCE
    if campaign.website_analysis and campaign.website_analysis.status == AnalysisStatus.COMPLETED:
        return STAGE_INTELLIGENCE
    return STAGE_WEBSITE


def to_out(campaign: Campaign) -> CampaignOut:
    from app.services.csv.reupload_service import icp_fingerprint

    out = CampaignOut.model_validate(campaign)
    out.stage = derive_stage(campaign)
    out.analysis_status = campaign.website_analysis.status.value if campaign.website_analysis else None
    out.import_status = campaign.lead_import.status.value if campaign.lead_import else None
    out.website_analysis_public_id = campaign.website_analysis.public_id if campaign.website_analysis else None
    out.company_intelligence_public_id = campaign.company_intelligence.public_id if campaign.company_intelligence else None
    out.icp_public_id = campaign.icp.public_id if campaign.icp else None
    out.lead_import_public_id = campaign.lead_import.public_id if campaign.lead_import else None
    out.results_stale = bool(
        campaign.icp
        and campaign.lead_import
        and campaign.lead_import.icp_snapshot_hash
        and campaign.lead_import.icp_snapshot_hash != icp_fingerprint(campaign.icp)
    )
    return out
