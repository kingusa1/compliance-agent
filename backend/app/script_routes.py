import json
import os
import uuid
from datetime import datetime
from app._clock import utcnow

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.auth import current_user
from app.config import settings
from app.database import get_db
from app.reviewers import current_reviewer, require_lead
from app.models import Script, ScriptLineMapping, ScriptVersion
from app.schemas import ScriptCreate, ScriptListResponse, ScriptResponse, ScriptVersionListResponse, ScriptVersionResponse
from app.script_parser import parse_script_to_checkpoints, checkpoints_to_markdown

script_router = APIRouter()


@script_router.post("/api/scripts/upload")
async def upload_and_parse_script(
    file: UploadFile = File(...),
    _user: dict = Depends(require_lead),
):
    """Parse an uploaded script file into checkpoint JSON.

    2026-05-13 hardening: now uses the same 4-pass hardened extractor
    (strict prose → prose-mode retry → per-page split → deterministic
    heading fallback) as the bulk admin ingest endpoint, so the /scripts
    UI upload never yields 0 checkpoints for a non-trivial file.
    """
    allowed_types = {".pdf", ".docx", ".md", ".markdown", ".txt"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_types:
        raise HTTPException(
            400,
            f"Invalid file type: {ext}. Allowed: {', '.join(sorted(allowed_types))}",
        )

    os.makedirs(settings.upload_dir, exist_ok=True)
    temp_path = os.path.join(settings.upload_dir, f"script_{uuid.uuid4()}{ext}")
    content = await file.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    try:
        # Seed supplier/script names from the filename so the extractor
        # prompt has at least some context. The reviewer can override
        # both in the preview before saving.
        stem = os.path.splitext(os.path.basename(file.filename))[0]
        seed_script_name = stem.replace("_", " ").strip() or "Unknown"
        try:
            checkpoints = await parse_script_to_checkpoints(
                temp_path,
                supplier_name="Unknown",
                script_name=seed_script_name,
                script_type="acquisition",
            )
        except ValueError as ve:
            raise HTTPException(400, str(ve))
        except Exception as ee:
            raise HTTPException(
                500,
                f"Script extraction failed: {type(ee).__name__}: {ee}",
            )
        return {
            "filename": file.filename,
            "checkpoints": checkpoints,
            "checkpoint_count": len(checkpoints),
        }
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@script_router.post("/api/scripts", response_model=ScriptResponse)
def create_script(
    script: ScriptCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_lead),
):
    checkpoints_json = json.dumps([cp.model_dump() for cp in script.checkpoints])
    db_script = Script(
        id=str(uuid.uuid4()),
        supplier_name=script.supplier_name,
        script_name=script.script_name,
        version=script.version,
        mode=script.mode,
        checkpoints=checkpoints_json,
        active=True,
    )
    db.add(db_script)
    db.flush()  # flush to get db_script.id before creating version

    # Create initial version snapshot (version 1)
    initial_version = ScriptVersion(
        script_id=db_script.id,
        version_number=1,
        checkpoints_snapshot=checkpoints_json,
        mode_snapshot=script.mode or "meaning_for_meaning",
    )
    db.add(initial_version)
    # Audit row inside the same transaction so business write +
    # tamper-evident chain extension are atomic. Payload is structural
    # only — supplier/script names + checkpoint count, never the script body.
    record_audit(
        db,
        action="script.create",
        entity_type="script",
        entity_id=db_script.id,
        payload={
            "supplier_name": script.supplier_name,
            "script_name": script.script_name,
            "version": script.version,
            "mode": script.mode,
            "checkpoint_count": len(script.checkpoints),
        },
        actor_id=user.get("id") if isinstance(user, dict) else None,
    )
    db.commit()
    db.refresh(db_script)
    return db_script


@script_router.get("/api/scripts", response_model=ScriptListResponse)
def list_scripts(
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),
):
    scripts = db.query(Script).order_by(Script.created_at.desc()).all()
    return ScriptListResponse(scripts=scripts, total=len(scripts))


@script_router.get("/api/scripts/{script_id}", response_model=ScriptResponse)
def get_script(
    script_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),
):
    script = db.query(Script).filter_by(id=script_id).first()
    if not script:
        raise HTTPException(404, "Script not found")
    return script


@script_router.get("/api/scripts/{script_id}/markdown", response_class=PlainTextResponse)
def get_script_markdown(
    script_id: str,
    download: bool = False,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),
):
    """Return the script rendered as clean markdown — what the agent actually sees.
    Set ?download=true to trigger a file download instead of inline preview."""
    script = db.query(Script).filter_by(id=script_id).first()
    if not script:
        raise HTTPException(404, "Script not found")
    try:
        checkpoints = json.loads(script.checkpoints or "[]")
    except Exception:
        checkpoints = []
    md = checkpoints_to_markdown(script.supplier_name, script.script_name, script.mode or "meaning_for_meaning", checkpoints)
    headers = {}
    if download:
        safe_name = f"{script.supplier_name}__{script.script_name}".replace(" ", "_").replace("/", "_")
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}.md"'
    return Response(content=md, media_type="text/markdown; charset=utf-8", headers=headers)


@script_router.put("/api/scripts/{script_id}", response_model=ScriptResponse)
def update_script(
    script_id: str,
    script: ScriptCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_lead),
):
    db_script = db.query(Script).filter_by(id=script_id).first()
    if not db_script:
        raise HTTPException(404, "Script not found")

    new_checkpoints_json = json.dumps([cp.model_dump() for cp in script.checkpoints])
    checkpoints_changed = db_script.checkpoints != new_checkpoints_json
    mode_changed = db_script.mode != (script.mode or "meaning_for_meaning")

    # Track which top-level fields actually changed for the audit payload.
    fields_touched: list[str] = []
    if db_script.supplier_name != script.supplier_name:
        fields_touched.append("supplier_name")
    if db_script.script_name != script.script_name:
        fields_touched.append("script_name")
    if db_script.version != script.version:
        fields_touched.append("version")
    if mode_changed:
        fields_touched.append("mode")
    if checkpoints_changed:
        fields_touched.append("checkpoints")

    if checkpoints_changed or mode_changed:
        # Determine next version number
        from sqlalchemy import func
        max_version = db.query(func.max(ScriptVersion.version_number)).filter(
            ScriptVersion.script_id == script_id
        ).scalar() or 0

        new_version = ScriptVersion(
            script_id=script_id,
            version_number=max_version + 1,
            checkpoints_snapshot=db_script.checkpoints,  # snapshot of OLD state
            mode_snapshot=db_script.mode,
        )
        db.add(new_version)

    db_script.supplier_name = script.supplier_name
    db_script.script_name = script.script_name
    db_script.version = script.version
    db_script.mode = script.mode
    db_script.checkpoints = new_checkpoints_json
    db_script.updated_at = utcnow()
    # Structural payload only — list of changed field names, never script body.
    record_audit(
        db,
        action="script.update",
        entity_type="script",
        entity_id=db_script.id,
        payload={
            "fields_touched": fields_touched,
            "checkpoint_count": len(script.checkpoints),
        },
        actor_id=user.get("id") if isinstance(user, dict) else None,
    )
    db.commit()
    db.refresh(db_script)
    return db_script


@script_router.delete("/api/scripts/{script_id}")
def delete_script(
    script_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_lead),
):
    db_script = db.query(Script).filter_by(id=script_id).first()
    if not db_script:
        raise HTTPException(404, "Script not found")

    db_script.active = False
    db_script.updated_at = utcnow()
    # Delete is soft (active=False); record action so the chain captures the
    # deactivation. Empty structural payload — id alone identifies the row.
    record_audit(
        db,
        action="script.delete",
        entity_type="script",
        entity_id=db_script.id,
        payload={
            "supplier_name": db_script.supplier_name,
            "script_name": db_script.script_name,
        },
        actor_id=user.get("id") if isinstance(user, dict) else None,
    )
    db.commit()
    return {"status": "deactivated"}


@script_router.get("/api/scripts/{script_id}/versions", response_model=ScriptVersionListResponse)
def list_script_versions(
    script_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),
):
    db_script = db.query(Script).filter_by(id=script_id).first()
    if not db_script:
        raise HTTPException(404, "Script not found")

    versions = (
        db.query(ScriptVersion)
        .filter(ScriptVersion.script_id == script_id)
        .order_by(ScriptVersion.version_number.asc())
        .all()
    )
    return ScriptVersionListResponse(versions=versions, total=len(versions))


@script_router.get("/api/scripts/{script_id}/lines")
def get_script_lines(
    script_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(current_user),
):
    """W4.3 — return the script body as numbered lines, joined with
    `script_line_mappings` (W4.2) on (supplier, script_section) so the UI
    can overlay "[L17] prices EXCLUDE VAT" badges next to known checkpoint
    lines.

    Response shape::

        [
          {"line_number": 1, "text": "...", "checkpoint_name": "...", "internal_key": "..."},
          {"line_number": 2, "text": "...", "checkpoint_name": null,  "internal_key": null},
          ...
          {"line_number": null, "text": "", "checkpoint_name": "...", "internal_key": "..."},
        ]

    `line_number=null` rows are mapping entries with no concrete line ref
    (LOA-wide checkpoints like `loa_dob_confirmation`); they're appended
    after the numbered lines so reviewers still see the full set.
    """
    script = db.query(Script).filter_by(id=script_id).first()
    if not script:
        raise HTTPException(404, "Script not found")

    # Render markdown body the same way GET /markdown does — single source of truth.
    try:
        checkpoints = json.loads(script.checkpoints or "[]")
    except Exception:
        checkpoints = []
    md = checkpoints_to_markdown(
        script.supplier_name,
        script.script_name,
        script.mode or "meaning_for_meaning",
        checkpoints,
    )

    # Pull mappings for this (supplier, script_section). script_section is
    # matched against script.script_name (e.g. "EON Verbal", "LOA").
    mappings = (
        db.query(ScriptLineMapping)
        .filter(
            ScriptLineMapping.supplier == script.supplier_name,
            ScriptLineMapping.script_section == script.script_name,
        )
        .all()
    )
    by_line: dict[int, ScriptLineMapping] = {
        m.line_number: m for m in mappings if m.line_number is not None
    }
    section_wide = [m for m in mappings if m.line_number is None]

    out: list[dict] = []
    # split() on bare "\n" preserves empty lines from the markdown body so
    # line numbers match what a user would count in a text editor.
    for idx, text in enumerate(md.split("\n"), start=1):
        m = by_line.get(idx)
        out.append({
            "line_number": idx,
            "text": text,
            "checkpoint_name": m.checkpoint_name if m else None,
            "internal_key": m.internal_key if m else None,
        })
    for m in section_wide:
        out.append({
            "line_number": None,
            "text": "",
            "checkpoint_name": m.checkpoint_name,
            "internal_key": m.internal_key,
        })
    return out
