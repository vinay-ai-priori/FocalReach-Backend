from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.api.ownership import get_owned_import
from app.models.user import User
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
from app.services.csv.column_matcher import match_columns
from app.services.csv.field_definitions import FIELD_DEFINITIONS
from app.services.csv.dedup_service import compute_dedup_stats
from app.services.csv.import_service import compute_stats, confirm_import, parse_and_validate, update_mapping
from app.tasks.qualification_tasks import qualify_import_task

router = APIRouter(prefix="/imports", tags=["lead-imports"])


def _validation_out(db: Session, lead_import) -> ImportValidationOut:
    confidences = {}
    if lead_import.raw_columns:
        confidences = match_columns(lead_import.raw_columns)
    field_mappings = [
        FieldMapping(
            canonical_field=f.key,
            label=f.label,
            csv_column=lead_import.column_mapping.get(f.key),
            confidence=(confidences.get(f.key) or {}).get("confidence", 0.0)
            if lead_import.column_mapping.get(f.key)
            else 0.0,
            required_for=f.required_for,
            is_mandatory=f.is_mandatory,
        )
        for f in FIELD_DEFINITIONS
    ]
    return ImportValidationOut(
        lead_import=LeadImportOut.model_validate(lead_import),
        field_mappings=field_mappings,
        missing_fields=[MissingFieldWarning(**m) for m in lead_import.missing_fields],
        sample_rows=(lead_import.raw_rows or [])[:5],
        stats=ImportStats(**compute_stats(lead_import), **compute_dedup_stats(db, lead_import)),
    )


@router.post("/upload", response_model=ImportValidationOut)
def upload_csv(
    icp_id: int = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportValidationOut:
    if not (file.filename or "").lower().endswith(".csv"):
        raise ValidationFailedError("Only .csv files are supported.")
    icp = ICPRepository(db).get(icp_id)
    if not icp:
        raise NotFoundError(f"ICP {icp_id} not found.")
    lead_import = parse_and_validate(
        db, icp, file.filename, file.file.read(), user_id=user.id, organization_id=user.organization_id
    )
    return _validation_out(db, lead_import)


@router.get("/{import_id}", response_model=LeadImportOut)
def get_import(import_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LeadImportOut:
    return LeadImportOut.model_validate(get_owned_import(db, import_id, user))


@router.get("/{import_id}/validation", response_model=ImportValidationOut)
def get_validation(
    import_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ImportValidationOut:
    return _validation_out(db, get_owned_import(db, import_id, user))


@router.patch("/{import_id}/mapping", response_model=ImportValidationOut)
def patch_mapping(
    import_id: int,
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
def confirm(import_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskAccepted:
    """User acknowledged the validation report: materialize rows, then qualify companies via Celery."""
    lead_import = get_owned_import(db, import_id, user)
    lead_import = confirm_import(db, lead_import)
    task = qualify_import_task.delay(lead_import.id)
    return TaskAccepted(task_id=task.id, status="qualifying", resource_id=lead_import.id)
