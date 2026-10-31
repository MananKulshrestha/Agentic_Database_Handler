"""One-time restoration of an encrypted workspace supplied by the deploy owner."""

import gzip
import json
import os
from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy import DateTime, select

from .connectors import encrypt_password
from .database import AuditEvent, Base, Project, SessionLocal, uid

_EXCLUDED = {"users", "user_sessions", "project_members"}


async def restore_workspace():
    payload = os.environ.get("WORKSPACE_RESTORE_PAYLOAD") or "".join(
        os.environ.get(f"WORKSPACE_RESTORE_PAYLOAD_{part}", "") for part in range(1, 9)
    )
    key = os.environ.get("WORKSPACE_RESTORE_KEY")
    if not payload or not key:
        return
    data = json.loads(gzip.decompress(Fernet(key.encode()).decrypt(payload.encode())))
    marker = "workspace.restore:" + data["export_id"]
    tables = data["tables"]
    if set(tables) - (set(Base.metadata.tables) - _EXCLUDED):
        raise ValueError("Workspace restore contains unsupported tables")

    async with SessionLocal() as db, db.begin():
        if await db.scalar(select(AuditEvent.id).where(AuditEvent.action == marker)):
            return
        # Never merge an export into an already populated remote workspace.
        for table in Base.metadata.sorted_tables:
            if table.name != "project_settings" and await db.scalar(select(table.c.id).limit(1)):
                raise ValueError("Workspace restore requires an empty destination")

        project = (await db.scalars(select(Project))).first()
        source_projects = tables.get("project_settings", [])
        if len(source_projects) != 1:
            raise ValueError("Workspace restore requires exactly one source project")
        source_project = source_projects[0]
        if project:
            project.name = source_project["name"]
            project.description = source_project["description"]
            project_id = project.id
        else:
            project_id = source_project["id"]
            db.add(Project(**source_project))
        await db.flush()

        for table in Base.metadata.sorted_tables:
            if table.name in _EXCLUDED or table.name == "project_settings":
                continue
            for source_row in tables.get(table.name, []):
                row = dict(source_row)
                if table.name == "connections":
                    row["password_encrypted"] = encrypt_password(row.pop("restore_password"))
                for column in table.columns:
                    value = row.get(column.name)
                    if isinstance(column.type, DateTime) and isinstance(value, str):
                        row[column.name] = datetime.fromisoformat(value)
                    if any(fk.target_fullname == "project_settings.id" for fk in column.foreign_keys):
                        row[column.name] = project_id
                await db.execute(table.insert().values(**row))

        db.add(AuditEvent(
            id=uid(), action=marker, resource_type="project", resource_id=project_id,
            actor="local_user", details={"restored": True},
        ))
