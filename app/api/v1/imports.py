import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.api.ownership import get_owned_import
from app.models.user import User
from app.repositories.campaign_repository import CampaignRepository
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.lead_import import ImportStatus
from app.repositories.icp_repository import ICPRepository
from app.repositories.lead_import_repository import LeadImportRepository
from app.schemas.common import TaskAccepted
from app.schemas.lead_import import (
    ColumnMappingUpdate,
    FieldMapping,
    ImportStats,
    ImportValidationOut,
    LeadImportOut,
    MissingFieldWarning,
)
from app.services.csv.field_definitions import FIELD_DEFINITIONS
from app.services.csv.dedup_service import compute_dedup_stats
from app.services.csv.import_service import (
    compute_stats,
    confirm_import,
    confirm_reupload,
    parse_and_validate,
    update_mapping,
)
from app.services.csv.reupload_service import resolve_upload_mode
from app.tasks.qualification_tasks import qualify_import_task

router = APIRouter(prefix="/imports", tags=["lead-imports"])

ALLOWED_EXTENSIONS = (".csv", ".xlsx")
MAX_UPLOAD_MB = 10
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
_READ_CHUNK = 1024 * 1024  # 1 MB


def _read_capped(file: UploadFile) -> bytes:
    """Read the upload in chunks, aborting as soon as the size cap is crossed —
    never loads an oversized file fully into memory."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ValidationFailedError(
                f"File is larger than the {MAX_UPLOAD_MB} MB upload limit. "
                "Split the file or remove unused columns and try again."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _lead_import_out(lead_import) -> LeadImportOut:
    out = LeadImportOut.model_validate(lead_import)
    out.icp_public_id = lead_import.icp.public_id if lead_import.icp else None
    return out


def _resolve_reupload(db: Session, lead_import) -> dict:
    """Mode/counts for a pending re-upload, recomputed from the CURRENT mapping so the
    verdict refreshes whenever the user remaps a column."""
    if lead_import.campaign_id is None:
        return {}
    campaign = CampaignRepository(db).get(lead_import.campaign_id)
    if not campaign or not campaign.lead_import_id:
        return {}
    permanent = LeadImportRepository(db).get(campaign.lead_import_id)
    icp = ICPRepository(db).get(lead_import.icp_id)
    if not permanent or not icp:
        return {}
    resolution = resolve_upload_mode(
        db, permanent, icp, lead_import.raw_rows or [], lead_import.column_mapping or {}
    )
    return {
        "upload_mode": resolution["mode"],
        "inputs_changed": resolution["inputs_changed"],
        "campaign_new_leads": resolution["new_leads"],
        "campaign_existing_leads": resolution["existing_leads"],
    }


def _validation_out(db: Session, lead_import) -> ImportValidationOut:
    meta = lead_import.mapping_meta or {}
    field_mappings = [
        FieldMapping(
            canonical_field=f.key,
            label=f.label,
            csv_column=lead_import.column_mapping.get(f.key),
            confidence=(meta.get(f.key) or {}).get("confidence", 0.0)
            if lead_import.column_mapping.get(f.key)
            else 0.0,
            source=(meta.get(f.key) or {}).get("source")
            if lead_import.column_mapping.get(f.key)
            else None,
            required_for=f.required_for,
            is_mandatory=f.is_mandatory,
        )
        for f in FIELD_DEFINITIONS
    ]
    return ImportValidationOut(
        lead_import=_lead_import_out(lead_import),
        field_mappings=field_mappings,
        missing_fields=[MissingFieldWarning(**m) for m in lead_import.missing_fields],
        sample_rows=(lead_import.raw_rows or [])[:5],
        stats=ImportStats(**compute_stats(lead_import), **compute_dedup_stats(db, lead_import)),
        **_resolve_reupload(db, lead_import),
    )


@router.post("/upload", response_model=ImportValidationOut)
def upload_csv(
    icp_id: UUID = Form(...),
    campaign_id: UUID | None = Form(default=None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportValidationOut:
    filename = (file.filename or "").lower()
    if not filename.endswith(ALLOWED_EXTENSIONS):
        raise ValidationFailedError(
            f"Unsupported file type. Accepted formats: {', '.join(ALLOWED_EXTENSIONS)} (max {MAX_UPLOAD_MB} MB)."
        )
    # Cheap early rejection via the declared size (spoofable, so the chunked read below
    # remains the hard limit).
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise ValidationFailedError(
            f"File is larger than the {MAX_UPLOAD_MB} MB upload limit. "
            "Split the file or remove unused columns and try again."
        )
    file_bytes = _read_capped(file)
    icp = ICPRepository(db).get_by_public_id(icp_id)
    if not icp:
        raise NotFoundError(f"ICP {icp_id} not found.")

    campaign = CampaignRepository(db).get_by_public_id(campaign_id) if campaign_id else None
    if campaign and campaign.user_id != user.id:
        campaign = None

    permanent = None
    if campaign and campaign.lead_import_id:
        permanent = LeadImportRepository(db).get(campaign.lead_import_id)
        if permanent and permanent.status == ImportStatus.MAPPING:
            # Previous upload was never confirmed — replace it instead of re-uploading into it.
            permanent = None

    if permanent is not None:
        # One run at a time: no re-upload while the pipeline is processing.
        if permanent.status in (ImportStatus.IMPORTED, ImportStatus.QUALIFYING, ImportStatus.SCORING):
            raise ValidationFailedError(
                "This campaign's pipeline is still running. Wait for it to finish before uploading again."
            )
        # Discard any earlier pending upload the user abandoned without confirming.
        from app.models.lead_import import LeadImport as LeadImportModel

        for stale in db.query(LeadImportModel).filter(
            LeadImportModel.campaign_id == campaign.id, LeadImportModel.status == ImportStatus.MAPPING
        ):
            db.delete(stale)
        db.commit()
        # Pending re-upload: campaign keeps pointing at its permanent import until confirm.
        lead_import = parse_and_validate(
            db, icp, file.filename, file_bytes,
            user_id=user.id, organization_id=user.organization_id, campaign_id=campaign.id,
        )
    else:
        lead_import = parse_and_validate(
            db, icp, file.filename, file_bytes, user_id=user.id, organization_id=user.organization_id
        )
        if campaign:
            CampaignRepository(db).update(campaign, lead_import_id=lead_import.id)
    return _validation_out(db, lead_import)


@router.get("/{import_id}", response_model=LeadImportOut)
def get_import(
    import_id: UUID,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadImportOut:
    # This endpoint is polled every ~1.2s while an import is qualifying/scoring. Most polls
    # land between DB writes, so the payload is byte-identical to what the client already has —
    # ETag lets those return a bodyless 304 instead of resending the same JSON over and over.
    lead_import = get_owned_import(db, import_id, user)
    out = _lead_import_out(lead_import)
    etag = hashlib.md5(out.model_dump_json().encode()).hexdigest()
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)  # bypasses response_model — no body, as required
    response.headers["ETag"] = etag
    return out


@router.get("/{import_id}/validation", response_model=ImportValidationOut)
def get_validation(
    import_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ImportValidationOut:
    return _validation_out(db, get_owned_import(db, import_id, user))


@router.patch("/{import_id}/mapping", response_model=ImportValidationOut)
def patch_mapping(
    import_id: UUID,
    payload: ColumnMappingUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportValidationOut:
    lead_import = get_owned_import(db, import_id, user)
    if lead_import.status != ImportStatus.MAPPING:
        raise ValidationFailedError("Column mapping can only be changed before the import is confirmed.")
    lead_import = update_mapping(db, lead_import, payload.column_mapping)
    return _validation_out(db, lead_import)


@router.post("/{import_id}/confirm", response_model=TaskAccepted)
def confirm(import_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskAccepted:
    """User acknowledged the validation report: materialize rows, then qualify companies via Celery."""
    lead_import = get_owned_import(db, import_id, user)

    if lead_import.campaign_id is not None:
        # Pending re-upload: merge into the campaign's permanent import (append/rerun; mode
        # is re-resolved server-side — blocked uploads are rejected here too).
        campaign = CampaignRepository(db).get(lead_import.campaign_id)
        permanent = LeadImportRepository(db).get(campaign.lead_import_id) if campaign else None
        if not permanent:
            raise ValidationFailedError("The campaign this upload belongs to no longer has an active import.")
        permanent, _mode = confirm_reupload(db, lead_import, permanent)
        task = qualify_import_task.delay(permanent.id)
        return TaskAccepted(task_id=task.id, status="qualifying", resource_id=permanent.public_id)

    lead_import = confirm_import(db, lead_import)
    task = qualify_import_task.delay(lead_import.id)
    return TaskAccepted(task_id=task.id, status="qualifying", resource_id=lead_import.public_id)
