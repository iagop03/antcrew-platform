"""Workspace contract schema management — Phase 1 custom_fields extension."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, require_role, get_workspace_context, WorkspaceContext, ws_accessible
from app.core.database import get_session
from app.core.exceptions import WorkspaceNotFoundError
from app.models.run import Workspace, WorkspaceContractSchema

router = APIRouter(
    prefix="/workspaces",
    tags=["contract-schemas"],
    dependencies=[Depends(require_api_key)],
)

# Contracts that have a custom_fields extension point in Phase 1.
# Expand this set as custom_fields is added to other artifact models.
EXTENDABLE_CONTRACTS: frozenset[str] = frozenset({"PRD"})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ContractSchemaIn(BaseModel):
    json_schema: dict[str, Any]
    description: Optional[str] = None


class ContractSchemaOut(BaseModel):
    workspace_id: int
    contract_name: str
    json_schema: dict[str, Any]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: WorkspaceContractSchema) -> "ContractSchemaOut":
        return cls(
            workspace_id=row.workspace_id,
            contract_name=row.contract_name,
            json_schema=row.json_schema or {},
            description=row.description,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/contract-schemas", response_model=list[ContractSchemaOut],
            dependencies=[Depends(require_role("admin", "read"))])
async def list_contract_schemas(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[ContractSchemaOut]:
    """List all custom_fields schemas defined for this workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise WorkspaceNotFoundError(workspace_id)
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise WorkspaceNotFoundError(workspace_id)
    rows = (await session.exec(
        select(WorkspaceContractSchema).where(WorkspaceContractSchema.workspace_id == workspace_id)
    )).all()
    return [ContractSchemaOut.from_row(r) for r in rows]


@router.get("/{workspace_id}/contract-schemas/{contract_name}", response_model=ContractSchemaOut,
            dependencies=[Depends(require_role("admin", "read"))])
async def get_contract_schema(
    workspace_id: int,
    contract_name: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> ContractSchemaOut:
    """Get the custom_fields JSON Schema for a specific contract."""
    from app.core.exceptions import NotAccessibleError
    from fastapi import HTTPException
    if not ws_accessible(workspace_id, ctx):
        raise NotAccessibleError()
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise WorkspaceNotFoundError(workspace_id)
    row = (await session.exec(
        select(WorkspaceContractSchema)
        .where(WorkspaceContractSchema.workspace_id == workspace_id)
        .where(WorkspaceContractSchema.contract_name == contract_name)
    )).first()
    if not row:
        raise HTTPException(404, f"No schema defined for contract {contract_name!r} in workspace {workspace_id}")
    return ContractSchemaOut.from_row(row)


@router.put("/{workspace_id}/contract-schemas/{contract_name}", response_model=ContractSchemaOut,
            dependencies=[Depends(require_role("admin"))])
async def upsert_contract_schema(
    workspace_id: int,
    contract_name: str,
    body: ContractSchemaIn,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> ContractSchemaOut:
    """Create or replace the custom_fields JSON Schema for a contract.

    contract_name must be one of the contracts that expose custom_fields:
    currently only "PRD" (Phase 1 pilot). The json_schema is stored as-is —
    no validation is enforced at runtime in Phase 1; the schema is informational
    and intended for prompt-injection in future phases.
    """
    from app.core.exceptions import NotAccessibleError
    from fastapi import HTTPException
    if not ws_accessible(workspace_id, ctx):
        raise NotAccessibleError()
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise WorkspaceNotFoundError(workspace_id)
    if contract_name not in EXTENDABLE_CONTRACTS:
        raise HTTPException(
            422,
            f"Contract {contract_name!r} does not expose custom_fields. "
            f"Extendable contracts: {sorted(EXTENDABLE_CONTRACTS)}",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = (await session.exec(
        select(WorkspaceContractSchema)
        .where(WorkspaceContractSchema.workspace_id == workspace_id)
        .where(WorkspaceContractSchema.contract_name == contract_name)
    )).first()

    if row:
        row.json_schema = body.json_schema
        row.description = body.description
        row.updated_at = now
    else:
        row = WorkspaceContractSchema(
            workspace_id=workspace_id,
            contract_name=contract_name,
            json_schema=body.json_schema,
            description=body.description,
            created_at=now,
            updated_at=now,
        )

    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ContractSchemaOut.from_row(row)


@router.delete("/{workspace_id}/contract-schemas/{contract_name}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_contract_schema(
    workspace_id: int,
    contract_name: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Remove the custom_fields schema for a contract from this workspace."""
    from app.core.exceptions import NotAccessibleError
    from fastapi import HTTPException
    if not ws_accessible(workspace_id, ctx):
        raise NotAccessibleError()
    row = (await session.exec(
        select(WorkspaceContractSchema)
        .where(WorkspaceContractSchema.workspace_id == workspace_id)
        .where(WorkspaceContractSchema.contract_name == contract_name)
    )).first()
    if not row:
        raise HTTPException(404, f"No schema for contract {contract_name!r} in workspace {workspace_id}")
    await session.delete(row)
    await session.commit()


@router.get("/contract-schemas/extendable", tags=["contract-schemas"])
async def list_extendable_contracts() -> dict:
    """Return the list of contracts that support custom_fields in Phase 1."""
    return {
        "extendable_contracts": sorted(EXTENDABLE_CONTRACTS),
        "phase": 1,
        "note": (
            "custom_fields is an unvalidated dict extension point on each listed contract. "
            "Define a JSON Schema per workspace via PUT /workspaces/{id}/contract-schemas/{contract}."
        ),
    }
