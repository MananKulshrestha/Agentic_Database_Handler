import asyncio
import json
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import String, cast, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from .analysis import RULES
from .auth import hash_password, new_token, token_digest, verify_password
from .config import ai_enabled, jev_enabled, settings
from .connectors import encrypt_password, test_connection
from .database import (
    Artifact,
    AuditEvent,
    Base,
    BusinessRule,
    ChangeApproval,
    Connection,
    ContextDocument,
    Conversation,
    ExperimentRecord,
    Finding,
    Message,
    Project,
    ProjectMember,
    Relationship,
    Run,
    RunEvent,
    Sandbox,
    Schedule,
    SchemaObject,
    SessionLocal,
    Snapshot,
    User,
    UserSession,
    engine,
    get_session,
    utcnow,
)
from .diff import compare_snapshots
from .error_messages import describe_error
from .jev import (
    DIFF_QUESTIONS,
    FINDING_QUESTIONS,
    PROPOSAL_QUESTIONS,
    RUN_QUESTIONS,
    JevUnavailable,
    decide,
)
from .jobs import add_event, manager

logger = logging.getLogger(__name__)
current_actor: ContextVar[str] = ContextVar("current_actor", default="local_user")
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_database_url.startswith("sqlite"):
        Path(".local").mkdir(exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    migration_config = Config(str(root / "alembic.ini"))
    migration_config.set_main_option("script_location", str(root / "backend" / "migrations"))
    async with engine.connect() as db:
        existing = await db.run_sync(lambda sync_db: inspect(sync_db).get_table_names())
    if existing and "alembic_version" not in existing:
        # Adopt databases created by the original local create_all startup.
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        await asyncio.to_thread(command.stamp, migration_config, "head")
    else:
        await asyncio.to_thread(command.upgrade, migration_config, "head")
    async with SessionLocal() as db:
        if not await db.scalar(select(Project.id)):
            db.add(Project())
            await db.commit()
    await manager.start()
    yield
    await manager.stop()
    await engine.dispose()


app = FastAPI(title="Agentic Database Handler", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    actor = "local_user"
    if settings.auth_required and request.url.path.startswith("/api/v1/") and request.method != "OPTIONS":
        public = ("/api/v1/health/", "/api/v1/auth/", "/api/v1/capabilities")
        if not request.url.path.startswith(public):
            raw = request.headers.get("authorization", "")
            token = raw[7:] if raw.lower().startswith("bearer ") else ""
            if not token:
                return JSONResponse(status_code=401, content={"code": "AUTH_REQUIRED", "message": "Sign in to continue", "details": None, "request_id": request_id}, headers={"X-Request-ID": request_id})
            async with SessionLocal() as db:
                session = await db.scalar(select(UserSession).where(UserSession.token_hash == token_digest(token), UserSession.expires_at > utcnow()))
                user = await db.get(User, session.user_id) if session else None
                project = (await db.scalars(select(Project))).first()
                membership = await db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)) if project and user else None
            if not user or not user.active or not membership:
                return JSONResponse(status_code=401, content={"code": "INVALID_SESSION", "message": "Sign in again", "details": None, "request_id": request_id}, headers={"X-Request-ID": request_id})
            request.state.user_id = user.id
            request.state.role = membership.role
            actor = user.id
            if request.method not in ("GET", "HEAD"):
                admin_prefixes = ("/api/v1/connections", "/api/v1/schedules", "/api/v1/sandboxes", "/api/v1/change-approvals", "/api/v1/project", "/api/v1/projects/")
                required_role = "admin" if request.url.path.startswith(admin_prefixes) else "analyst"
                allowed = membership.role == "admin" or (required_role == "analyst" and membership.role == "analyst")
                if not allowed:
                    return JSONResponse(status_code=403, content={"code": "FORBIDDEN", "message": f"{required_role} access is required", "details": None, "request_id": request_id}, headers={"X-Request-ID": request_id})
    actor_token = current_actor.set(actor)
    request_token = current_request_id.set(request_id)
    try:
        response = await call_next(request)
    finally:
        current_actor.reset(actor_token)
        current_request_id.reset(request_token)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": f"HTTP_{exc.status_code}",
            "message": detail,
            "details": exc.detail if not isinstance(exc.detail, str) else None,
            "request_id": request.state.request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": [
                {"field": list(error.get("loc", [])), "message": error.get("msg", "Invalid value"), "type": error.get("type", "validation_error")}
                for error in exc.errors()
            ],
            "request_id": request.state.request_id,
        },
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.error("Unhandled API error (%s)", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
            "details": None,
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


def out(item):
    return {
        column.key: getattr(item, column.key)
        for column in item.__table__.columns
        if column.key != "password_encrypted"
    }


def audit(db: AsyncSession, action: str, resource_type: str, resource_id: str, details: dict | None = None, actor: str | None = None):
    context = dict(details or {})
    if current_request_id.get():
        context["request_id"] = current_request_id.get()
    db.add(AuditEvent(action=action, resource_type=resource_type, resource_id=resource_id, actor=actor or current_actor.get(), details=context))


async def require(db, model, item_id):
    item = await db.get(model, item_id)
    if item is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return item


async def limited(db, statement, limit: int, cursor: int):
    return [
        out(item)
        for item in (await db.scalars(statement.offset(cursor).limit(limit))).all()
    ]


def require_jev():
    if not jev_enabled():
        raise HTTPException(503, "Set TYPESAFE_API_KEY and TYPESAFE_ALLOW_SCHEMA_SHARING=true to use Jev")


async def jev_batch(items: list[tuple[str, dict]], questions: dict) -> list[dict]:
    async def one(item_id: str, state: dict) -> dict:
        try:
            return {"id": item_id, **await decide(state, questions)}
        except JevUnavailable as exc:
            return {"id": item_id, "error": str(exc)}

    result = await asyncio.gather(*(one(item_id, state) for item_id, state in items))
    if result and all("error" in item for item in result):
        raise HTTPException(503, "Jev is unavailable; retry the decision later")
    return result


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10000)


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    engine: Literal["postgresql", "mariadb", "mongodb"]
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    auth_source: str = Field(default="admin", min_length=1, max_length=255)
    username: str = ""
    password: str = ""
    schema_scope: list[str] = Field(default_factory=list)


class ConnectionPatch(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = None
    auth_source: str | None = Field(default=None, min_length=1, max_length=255)
    username: str | None = None
    password: str | None = None
    schema_scope: list[str] | None = None


class FindingPatch(BaseModel):
    status: Literal["open", "accepted", "dismissed"]
    note: str = ""


class RuleCreate(BaseModel):
    connection_id: str
    object_name: str = ""
    description: str = Field(min_length=1)
    dependency: dict = Field(default_factory=dict)
    status: Literal["proposed", "confirmed"] = "proposed"


class RulePatch(BaseModel):
    object_name: str | None = None
    description: str | None = None
    dependency: dict | None = None
    status: Literal["proposed", "confirmed"] | None = None


class ConversationCreate(BaseModel):
    connection_id: str
    title: str = "New conversation"


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    snapshot_id: str
    object_ids: list[str] = Field(default_factory=list)
    mode: str = "explain"


class RunCreate(BaseModel):
    kind: Literal[
        "scan",
        "analyze",
        "document",
        "query_draft",
        "profile",
        "normalization",
        "query_plan",
        "query_execute",
        "suitability",
        "schema_diff",
        "redesign",
        "migration_proposal",
        "infer_relationships",
        "workload_analysis",
        "import_schema",
        "analyze_context",
        "experiment",
        "generate_demo_data",
    ]
    connection_id: str | None = None
    snapshot_id: str | None = None
    options: dict = Field(default_factory=dict)
    object_ids: list[str] = Field(default_factory=list)
    object_id: str | None = None
    question: str | None = None
    sql: str | None = None
    parameters: dict = Field(default_factory=dict)
    requested_level: str | None = None
    dependencies: list[dict] = Field(default_factory=list)
    workload_description: str | None = None
    before_snapshot_id: str | None = None
    after_snapshot_id: str | None = None
    finding_ids: list[str] = Field(default_factory=list)
    target_engine: str | None = None
    document_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    dialect: str | None = None
    column: str | None = None
    row_count: int | None = None
    sandbox_id: str | None = None


    @model_validator(mode="after")
    def check_required_inputs(self):
        required = {
            "scan": ("connection_id",),
            "analyze": ("snapshot_id",),
            "document": ("snapshot_id",),
            "query_draft": ("snapshot_id", "question"),
            "profile": ("snapshot_id", "object_ids"),
            "normalization": ("snapshot_id", "object_id"),
            "query_plan": ("connection_id", "sql"),
            "query_execute": ("connection_id", "sql"),
            "suitability": ("snapshot_id",),
            "schema_diff": ("before_snapshot_id", "after_snapshot_id"),
            "redesign": ("snapshot_id",),
            "migration_proposal": ("snapshot_id",),
            "infer_relationships": ("snapshot_id",),
            "workload_analysis": ("connection_id",),
            "import_schema": ("document_id",),
            "analyze_context": ("snapshot_id", "document_ids"),
            "experiment": ("snapshot_id", "object_id", "column"),
            "generate_demo_data": ("snapshot_id", "object_id"),
        }
        missing = [key for key in required[self.kind] if not getattr(self, key)]
        if missing:
            raise ValueError(f"{self.kind} requires {', '.join(missing)}")
        if self.row_count is not None and not 1 <= self.row_count <= 20000:
            raise ValueError("row_count must be between 1 and 20000")
        return self


class SandboxCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    connection_id: str
    acknowledge_temporary_writes: bool


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    connection_id: str
    interval_minutes: int = Field(ge=5, le=10080)
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    enabled: bool | None = None


class ApprovalCreate(BaseModel):
    artifact_id: str
    note: str = Field(default="", max_length=5000)


class ApprovalPatch(BaseModel):
    status: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=5000)


class LoginCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str


class BootstrapCreate(LoginCreate):
    password: str = Field(min_length=10, max_length=200)


class MemberCreate(BootstrapCreate):
    role: Literal["viewer", "analyst", "admin"] = "viewer"


class MemberPatch(BaseModel):
    role: Literal["viewer", "analyst", "admin"]


@app.get("/api/v1/health/live")
async def live():
    return {"status": "ok"}


@app.get("/api/v1/health/ready")
async def ready(db: AsyncSession = Depends(get_session)):
    await db.scalar(select(func.count(Project.id)))
    return {"status": "ready", "job_workers": len(manager.workers)}


@app.get("/api/v1/capabilities")
async def capabilities():
    return {
        "engines": ["postgresql", "mariadb", "mongodb"],
        "operations": list(RunCreate.model_fields["kind"].annotation.__args__),
        "ai_available": ai_enabled(),
        "ai_schema_sharing_enabled": settings.openai_allow_schema_sharing,
        "jev_enabled": jev_enabled(),
        "limits": {"active_jobs": settings.max_active_jobs, "query_rows": 500},
    }


@app.get("/api/v1/auth/status")
async def auth_status(db: AsyncSession = Depends(get_session)):
    has_users = bool(await db.scalar(select(User.id)))
    return {"required": settings.auth_required, "bootstrap_needed": settings.auth_required and not has_users}


@app.post("/api/v1/auth/bootstrap", status_code=201)
async def bootstrap_admin(body: BootstrapCreate, request: Request, db: AsyncSession = Depends(get_session)):
    if not settings.auth_required:
        raise HTTPException(409, "Authentication is not enabled")
    secret = request.headers.get("X-Bootstrap-Secret", "")
    if not settings.auth_bootstrap_secret:
        raise HTTPException(503, "Set AUTH_BOOTSTRAP_SECRET before creating the first administrator")
    if not secrets.compare_digest(secret, settings.auth_bootstrap_secret):
        raise HTTPException(403, "Invalid bootstrap secret")
    if await db.scalar(select(User.id)):
        raise HTTPException(409, "The first administrator already exists")
    project = (await db.scalars(select(Project))).first()
    if not project:
        project = Project()
        db.add(project)
        await db.flush()
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="admin"))
    audit(db, "auth.bootstrapped", "user", user.id, actor=user.id)
    await db.commit()
    return {"id": user.id, "username": user.username, "role": "admin"}


@app.post("/api/v1/auth/login")
async def login(body: LoginCreate, db: AsyncSession = Depends(get_session)):
    user = await db.scalar(select(User).where(User.username == body.username))
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    project = (await db.scalars(select(Project))).first()
    membership = await db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)) if project else None
    if not membership:
        raise HTTPException(403, "No project membership")
    token = new_token()
    db.add(UserSession(user_id=user.id, token_hash=token_digest(token), expires_at=utcnow() + timedelta(hours=24)))
    audit(db, "auth.login", "user", user.id, actor=user.id)
    await db.commit()
    return {"access_token": token, "token_type": "bearer", "expires_in": 86400, "user": {"id": user.id, "username": user.username, "role": membership.role}}


@app.post("/api/v1/auth/logout", status_code=204)
async def logout(request: Request, db: AsyncSession = Depends(get_session)):
    raw = request.headers.get("authorization", "")
    token = raw[7:] if raw.lower().startswith("bearer ") else ""
    if token:
        session = await db.scalar(select(UserSession).where(UserSession.token_hash == token_digest(token)))
        if session:
            audit(db, "auth.logout", "user", session.user_id, actor=session.user_id)
            await db.delete(session)
            await db.commit()


@app.get("/api/v1/auth/me")
async def auth_me(request: Request, db: AsyncSession = Depends(get_session)):
    raw = request.headers.get("authorization", "")
    token = raw[7:] if raw.lower().startswith("bearer ") else ""
    session = await db.scalar(select(UserSession).where(UserSession.token_hash == token_digest(token), UserSession.expires_at > utcnow())) if token else None
    user = await db.get(User, session.user_id) if session else None
    if not user or not user.active:
        raise HTTPException(401, "Sign in again")
    project = (await db.scalars(select(Project))).first()
    membership = await db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)) if project else None
    if not membership:
        raise HTTPException(403, "No project membership")
    return {"id": user.id, "username": user.username, "role": membership.role}


@app.get("/api/v1/project")
async def get_project(db: AsyncSession = Depends(get_session)):
    return out((await db.scalars(select(Project))).first())


@app.patch("/api/v1/project")
async def patch_project(body: ProjectPatch, db: AsyncSession = Depends(get_session)):
    item = (await db.scalars(select(Project))).first()
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await db.commit()
    return out(item)


@app.get("/api/v1/projects/{project_id}/members")
async def list_project_members(project_id: str, db: AsyncSession = Depends(get_session)):
    await require(db, Project, project_id)
    members = (await db.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id))).all()
    result = []
    for member in members:
        user = await db.get(User, member.user_id)
        result.append({"user_id": user.id, "username": user.username, "role": member.role, "active": user.active})
    return result


@app.post("/api/v1/projects/{project_id}/members", status_code=201)
async def create_project_member(project_id: str, body: MemberCreate, db: AsyncSession = Depends(get_session)):
    await require(db, Project, project_id)
    if await db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "Username already exists")
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    member = ProjectMember(project_id=project_id, user_id=user.id, role=body.role)
    db.add(member)
    audit(db, "member.created", "user", user.id, {"role": body.role})
    await db.commit()
    return {"user_id": user.id, "username": user.username, "role": body.role}


@app.patch("/api/v1/projects/{project_id}/members/{user_id}")
async def patch_project_member(project_id: str, user_id: str, body: MemberPatch, db: AsyncSession = Depends(get_session)):
    member = await db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id))
    if not member:
        raise HTTPException(404, "Member not found")
    if member.role == "admin" and body.role != "admin":
        admins = await db.scalar(select(func.count(ProjectMember.id)).where(ProjectMember.project_id == project_id, ProjectMember.role == "admin"))
        if admins <= 1:
            raise HTTPException(409, "A project needs at least one administrator")
    member.role = body.role
    audit(db, "member.role_changed", "user", user_id, {"role": body.role})
    await db.commit()
    return {"user_id": user_id, "role": member.role}


@app.delete("/api/v1/projects/{project_id}/members/{user_id}", status_code=204)
async def delete_project_member(project_id: str, user_id: str, db: AsyncSession = Depends(get_session)):
    member = await db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id))
    if not member:
        raise HTTPException(404, "Member not found")
    if member.role == "admin":
        admins = await db.scalar(select(func.count(ProjectMember.id)).where(ProjectMember.project_id == project_id, ProjectMember.role == "admin"))
        if admins <= 1:
            raise HTTPException(409, "A project needs at least one administrator")
    await db.delete(member)
    sessions = (await db.scalars(select(UserSession).where(UserSession.user_id == user_id))).all()
    for session in sessions:
        await db.delete(session)
    audit(db, "member.removed", "user", user_id)
    await db.commit()


@app.post("/api/v1/connections", status_code=201)
async def create_connection(
    body: ConnectionCreate, db: AsyncSession = Depends(get_session)
):
    values = body.model_dump(exclude={"password"})
    item = Connection(**values, password_encrypted=encrypt_password(body.password))
    db.add(item)
    await db.flush()
    audit(db, "connection.created", "connection", item.id, {"engine": item.engine})
    await db.commit()
    return out(item)


@app.get("/api/v1/connections")
async def list_connections(
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    return await limited(
        db,
        select(Connection)
        .where(Connection.archived == False)
        .order_by(Connection.created_at.desc()),
        limit,
        cursor,
    )


@app.get("/api/v1/connections/{connection_id}")
async def get_connection(connection_id: str, db: AsyncSession = Depends(get_session)):
    item = await require(db, Connection, connection_id)
    return out(item)


@app.patch("/api/v1/connections/{connection_id}")
async def patch_connection(
    connection_id: str, body: ConnectionPatch, db: AsyncSession = Depends(get_session)
):
    item = await require(db, Connection, connection_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(
            item,
            "password_encrypted" if key == "password" else key,
            encrypt_password(value) if key == "password" else value,
        )
    item.capabilities = {}
    audit(db, "connection.updated", "connection", item.id, {"fields": [key for key in body.model_fields_set if key != "password"]})
    await db.commit()
    return out(item)


@app.delete("/api/v1/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str, db: AsyncSession = Depends(get_session)
):
    item = await require(db, Connection, connection_id)
    item.archived = True
    item.password_encrypted = ""
    audit(db, "connection.archived", "connection", item.id)
    await db.commit()


@app.post("/api/v1/connections/{connection_id}/test")
async def check_connection(connection_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    item = await require(db, Connection, connection_id)
    try:
        result = await test_connection(item)
    except Exception as exc:
        failure = describe_error(exc, connection_test=True)
        logger.warning("Connection test failed (%s)", failure.code)
        return JSONResponse(
            status_code=422,
            content={
                "code": failure.code,
                "message": failure.message,
                "details": {"hint": failure.hint},
                "request_id": request.state.request_id,
            },
        )
    item.capabilities = result
    audit(db, "connection.tested", "connection", item.id, {"connected": True})
    await db.commit()
    return result


@app.post("/api/v1/sandboxes", status_code=201)
async def create_sandbox(body: SandboxCreate, db: AsyncSession = Depends(get_session)):
    if not body.acknowledge_temporary_writes:
        raise HTTPException(422, "Acknowledge that experiments create temporary data")
    connection = await require(db, Connection, body.connection_id)
    if connection.archived or connection.engine != "postgresql":
        raise HTTPException(422, "Database-native experiments currently need an active PostgreSQL connection")
    item = Sandbox(name=body.name, connection_id=body.connection_id)
    db.add(item)
    await db.commit()
    return out(item)


@app.get("/api/v1/sandboxes")
async def list_sandboxes(db: AsyncSession = Depends(get_session)):
    return [out(item) for item in (await db.scalars(select(Sandbox).where(Sandbox.archived == False).order_by(Sandbox.created_at.desc()))).all()]


@app.get("/api/v1/sandboxes/{sandbox_id}")
async def get_sandbox(sandbox_id: str, db: AsyncSession = Depends(get_session)):
    item = await require(db, Sandbox, sandbox_id)
    if item.archived:
        raise HTTPException(404, "Sandbox not found")
    return out(item)


@app.get("/api/v1/sandboxes/{sandbox_id}/experiments")
async def list_sandbox_experiments(
    sandbox_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    await require(db, Sandbox, sandbox_id)
    return await limited(db, select(ExperimentRecord).where(ExperimentRecord.sandbox_id == sandbox_id).order_by(ExperimentRecord.created_at.desc()), limit, cursor)


@app.delete("/api/v1/sandboxes/{sandbox_id}", status_code=204)
async def delete_sandbox(sandbox_id: str, db: AsyncSession = Depends(get_session)):
    item = await require(db, Sandbox, sandbox_id)
    item.archived = True
    await db.commit()


@app.post("/api/v1/schedules", status_code=201)
async def create_schedule(body: ScheduleCreate, db: AsyncSession = Depends(get_session)):
    connection = await require(db, Connection, body.connection_id)
    if connection.archived:
        raise HTTPException(422, "Connection is archived")
    item = Schedule(
        name=body.name,
        connection_id=body.connection_id,
        interval_minutes=body.interval_minutes,
        enabled=body.enabled,
        next_run_at=utcnow() + timedelta(minutes=body.interval_minutes),
    )
    db.add(item)
    await db.commit()
    return out(item)


@app.get("/api/v1/schedules")
async def list_schedules(db: AsyncSession = Depends(get_session)):
    return [out(item) for item in (await db.scalars(select(Schedule).order_by(Schedule.created_at.desc()))).all()]


@app.patch("/api/v1/schedules/{schedule_id}")
async def patch_schedule(schedule_id: str, body: SchedulePatch, db: AsyncSession = Depends(get_session)):
    item = await require(db, Schedule, schedule_id)
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(item, key, value)
    if "interval_minutes" in updates or updates.get("enabled"):
        item.next_run_at = utcnow() + timedelta(minutes=item.interval_minutes)
    await db.commit()
    return out(item)


@app.delete("/api/v1/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_session)):
    await db.delete(await require(db, Schedule, schedule_id))
    await db.commit()


@app.get("/api/v1/snapshots")
async def list_snapshots(
    connection_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    return await limited(
        db,
        select(Snapshot)
        .where(Snapshot.connection_id == connection_id)
        .order_by(Snapshot.created_at.desc()),
        limit,
        cursor,
    )


@app.get("/api/v1/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, db: AsyncSession = Depends(get_session)):
    return out(await require(db, Snapshot, snapshot_id))


@app.get("/api/v1/snapshots/{snapshot_id}/diff")
async def snapshot_diff(
    snapshot_id: str, against: str, db: AsyncSession = Depends(get_session)
):
    before = await require(db, Snapshot, against)
    after = await require(db, Snapshot, snapshot_id)
    if before.connection_id != after.connection_id:
        raise HTTPException(422, "Snapshots must belong to the same connection")
    old = (await db.scalars(select(SchemaObject).where(SchemaObject.snapshot_id == against))).all()
    new = (await db.scalars(select(SchemaObject).where(SchemaObject.snapshot_id == snapshot_id))).all()
    return {"before_snapshot_id": against, "after_snapshot_id": snapshot_id, **compare_snapshots(old, new)}


@app.get("/api/v1/jev/snapshots/{snapshot_id}/diff")
async def jev_snapshot_diff(snapshot_id: str, against: str, limit: int = Query(50, ge=1, le=50), cursor: int = Query(0, ge=0), db: AsyncSession = Depends(get_session)):
    require_jev()
    before = await require(db, Snapshot, against)
    after = await require(db, Snapshot, snapshot_id)
    if before.connection_id != after.connection_id:
        raise HTTPException(422, "Snapshots must belong to the same connection")
    old = (await db.scalars(select(SchemaObject).where(SchemaObject.snapshot_id == against))).all()
    new = (await db.scalars(select(SchemaObject).where(SchemaObject.snapshot_id == snapshot_id))).all()
    diff = compare_snapshots(old, new)
    changes = [
        {"id": f"{kind}:{item['schema']}.{item['name']}:{item['kind']}", "change": kind, **item}
        for kind in ("added", "removed", "changed") for item in diff[kind]
    ]
    page = changes[cursor:cursor + limit]
    items = [(item["id"], {"change": item["change"], "object": f"{item['schema']}.{item['name']}",
                            "kind": item["kind"], "details": json.dumps(item, default=str)[:4000]}) for item in page]
    return {"items": await jev_batch(items, DIFF_QUESTIONS), "changes": page,
            "before_snapshot_id": against, "after_snapshot_id": snapshot_id,
            "has_more": cursor + limit < len(changes)}


@app.get("/api/v1/snapshots/{snapshot_id}/objects")
async def list_objects(
    snapshot_id: str,
    kind: str | None = None,
    schema: str | None = None,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    await require(db, Snapshot, snapshot_id)
    stmt = select(SchemaObject).where(SchemaObject.snapshot_id == snapshot_id)
    if kind:
        stmt = stmt.where(SchemaObject.kind == kind)
    if schema:
        stmt = stmt.where(SchemaObject.schema_name == schema)
    if search:
        stmt = stmt.where(SchemaObject.name.ilike(f"%{search}%"))
    return await limited(
        db, stmt.order_by(SchemaObject.schema_name, SchemaObject.name), limit, cursor
    )


@app.get("/api/v1/snapshots/{snapshot_id}/objects/{object_id}")
async def get_object(
    snapshot_id: str, object_id: str, db: AsyncSession = Depends(get_session)
):
    item = await require(db, SchemaObject, object_id)
    if item.snapshot_id != snapshot_id:
        raise HTTPException(404, "Object not in snapshot")
    return out(item)


@app.get("/api/v1/snapshots/{snapshot_id}/graph")
async def graph(
    snapshot_id: str,
    schema: str | None = None,
    focus_object_id: str | None = None,
    depth: int = 1,
    db: AsyncSession = Depends(get_session),
):
    await require(db, Snapshot, snapshot_id)
    objects = (
        await db.scalars(
            select(SchemaObject).where(SchemaObject.snapshot_id == snapshot_id)
        )
    ).all()
    relationships = (
        await db.scalars(
            select(Relationship).where(Relationship.snapshot_id == snapshot_id)
        )
    ).all()
    if schema:
        objects = [o for o in objects if o.schema_name == schema]
    if focus_object_id:
        visible = {focus_object_id}
        for _ in range(min(depth, 3)):
            visible |= {
                r.target_object_id
                for r in relationships
                if r.source_object_id in visible
            }
            visible |= {
                r.source_object_id
                for r in relationships
                if r.target_object_id in visible
            }
        objects = [o for o in objects if o.id in visible]
    ids = {o.id for o in objects}
    return {
        "nodes": [out(o) for o in objects],
        "edges": [
            out(r)
            for r in relationships
            if r.source_object_id in ids and r.target_object_id in ids
        ],
    }


@app.get("/api/v1/rules")
async def rules(category: str | None = None):
    return [r for r in RULES if not category or r["category"] == category]


@app.get("/api/v1/findings")
async def list_findings(
    snapshot_id: str,
    severity: str | None = None,
    category: str | None = None,
    status: str | None = None,
    object_id: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Finding).where(Finding.snapshot_id == snapshot_id)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if category:
        stmt = stmt.where(Finding.category == category)
    if status:
        stmt = stmt.where(Finding.status == status)
    if object_id:
        try:
            object_id = str(uuid.UUID(object_id))
        except ValueError as exc:
            raise HTTPException(422, "object_id must be a UUID") from exc
        stmt = stmt.where(cast(Finding.object_ids, String).like(f'%"{object_id}"%'))
    return await limited(db, stmt.order_by(Finding.severity, Finding.id), limit, cursor)


@app.get("/api/v1/jev/findings")
async def jev_findings(snapshot_id: str, limit: int = Query(50, ge=1, le=50), cursor: int = Query(0, ge=0), db: AsyncSession = Depends(get_session)):
    require_jev()
    await require(db, Snapshot, snapshot_id)
    page = (await db.scalars(select(Finding).where(Finding.snapshot_id == snapshot_id)
                             .order_by(Finding.id).offset(cursor).limit(limit + 1))).all()
    findings = page[:limit]
    items = [(f.id, {"rule": f.rule_id, "category": f.category, "severity": f.severity,
                     "certainty": f.certainty, "review_status": f.status, "observation": f.observation[:1000],
                     "recommendation": f.recommendation[:1000], "assumptions": f.assumptions,
                     "evidence": json.dumps(f.evidence, default=str)[:3000]}) for f in findings]
    result = await jev_batch(items, FINDING_QUESTIONS)
    observations = {f.id: f.observation for f in findings}
    for item in result:
        item["observation"] = observations[item["id"]]
    result.sort(key=lambda item: item.get("decisions", {}).get("priority", {}).get("value", -1), reverse=True)
    return {"items": result, "snapshot_id": snapshot_id, "limit": limit, "cursor": cursor,
            "has_more": len(page) > limit}


@app.get("/api/v1/findings/{finding_id}")
async def get_finding(finding_id: str, db: AsyncSession = Depends(get_session)):
    return out(await require(db, Finding, finding_id))


@app.patch("/api/v1/findings/{finding_id}")
async def patch_finding(
    finding_id: str, body: FindingPatch, db: AsyncSession = Depends(get_session)
):
    item = await require(db, Finding, finding_id)
    item.status, item.note = body.status, body.note
    await db.commit()
    return out(item)


@app.get("/api/v1/business-rules")
async def list_business_rules(
    connection_id: str,
    object_name: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    stmt = select(BusinessRule).where(BusinessRule.connection_id == connection_id)
    if object_name:
        stmt = stmt.where(BusinessRule.object_name == object_name)
    return [out(x) for x in (await db.scalars(stmt)).all()]


@app.post("/api/v1/business-rules", status_code=201)
async def create_business_rule(
    body: RuleCreate, db: AsyncSession = Depends(get_session)
):
    await require(db, Connection, body.connection_id)
    item = BusinessRule(**body.model_dump())
    db.add(item)
    await db.commit()
    return out(item)


@app.patch("/api/v1/business-rules/{rule_id}")
async def patch_business_rule(
    rule_id: str, body: RulePatch, db: AsyncSession = Depends(get_session)
):
    item = await require(db, BusinessRule, rule_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    item.revision += 1
    await db.commit()
    return out(item)


@app.delete("/api/v1/business-rules/{rule_id}", status_code=204)
async def delete_business_rule(rule_id: str, db: AsyncSession = Depends(get_session)):
    await db.delete(await require(db, BusinessRule, rule_id))
    await db.commit()


@app.get("/api/v1/artifacts")
async def list_artifacts(
    connection_id: str | None = None,
    snapshot_id: str | None = None,
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Artifact)
    if connection_id:
        stmt = stmt.where(Artifact.connection_id == connection_id)
    if snapshot_id:
        stmt = stmt.where(Artifact.snapshot_id == snapshot_id)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    items = (
        await db.scalars(
            stmt.order_by(Artifact.created_at.desc()).offset(cursor).limit(limit)
        )
    ).all()
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "title": item.title,
            "connection_id": item.connection_id,
            "snapshot_id": item.snapshot_id,
            "created_at": item.created_at,
        }
        for item in items
    ]


@app.get("/api/v1/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, db: AsyncSession = Depends(get_session)):
    return out(await require(db, Artifact, artifact_id))


@app.get("/api/v1/jev/artifacts/{artifact_id}/readiness")
async def jev_proposal_readiness(artifact_id: str, db: AsyncSession = Depends(get_session)):
    require_jev()
    item = await require(db, Artifact, artifact_id)
    if item.kind not in ("redesign", "migration_proposal", "relationship_suggestions"):
        raise HTTPException(422, "Only design proposals can be assessed")
    state = {"kind": item.kind, "title": item.title,
             "proposal": json.dumps(item.content, default=str)[:8000],
             "note": "This is a review artifact; no database change has been applied."}
    try:
        return {"artifact_id": item.id, **await decide(state, PROPOSAL_QUESTIONS)}
    except JevUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/v1/artifacts/{artifact_id}/export")
async def export_artifact(
    artifact_id: str,
    format: Literal["markdown", "json"] = "markdown",
    db: AsyncSession = Depends(get_session),
):
    item = await require(db, Artifact, artifact_id)
    audit(db, "artifact.exported", "artifact", item.id, {"format": format})
    await db.commit()
    if format == "markdown":
        return Response(
            item.markdown,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f"attachment; filename=artifact-{item.id}.md"
            },
        )
    return Response(
        json.dumps(item.content, default=str, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=artifact-{item.id}.json"
        },
    )


@app.get("/api/v1/audit-events")
async def list_audit_events(
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    statement = select(AuditEvent)
    if resource_type:
        statement = statement.where(AuditEvent.resource_type == resource_type)
    if resource_id:
        statement = statement.where(AuditEvent.resource_id == resource_id)
    return await limited(db, statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id), limit, cursor)


@app.post("/api/v1/change-approvals", status_code=201)
async def create_change_approval(body: ApprovalCreate, db: AsyncSession = Depends(get_session)):
    item = await require(db, Artifact, body.artifact_id)
    if item.kind not in ("redesign", "migration_proposal", "relationship_suggestions"):
        raise HTTPException(422, "Only design proposals can be reviewed")
    approval = ChangeApproval(artifact_id=item.id, note=body.note)
    db.add(approval)
    await db.flush()
    audit(db, "approval.created", "change_approval", approval.id, {"artifact_id": item.id})
    await db.commit()
    return out(approval)


@app.get("/api/v1/change-approvals")
async def list_change_approvals(
    artifact_id: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    statement = select(ChangeApproval)
    if artifact_id:
        statement = statement.where(ChangeApproval.artifact_id == artifact_id)
    return [out(item) for item in (await db.scalars(statement.order_by(ChangeApproval.created_at.desc()))).all()]


@app.patch("/api/v1/change-approvals/{approval_id}")
async def review_change_approval(
    approval_id: str, body: ApprovalPatch, db: AsyncSession = Depends(get_session)
):
    item = await require(db, ChangeApproval, approval_id)
    if item.status != "pending":
        raise HTTPException(409, "This proposal has already been reviewed")
    item.status, item.note = body.status, body.note
    audit(db, "approval.reviewed", "change_approval", item.id, {"status": body.status})
    await db.commit()
    return out(item)


@app.post("/api/v1/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate, db: AsyncSession = Depends(get_session)
):
    await require(db, Connection, body.connection_id)
    item = Conversation(**body.model_dump())
    db.add(item)
    await db.commit()
    return out(item)


@app.get("/api/v1/conversations")
async def list_conversations(
    connection_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    return await limited(
        db,
        select(Conversation)
        .where(Conversation.connection_id == connection_id)
        .order_by(Conversation.created_at.desc()),
        limit,
        cursor,
    )


@app.get("/api/v1/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    item = await require(db, Conversation, conversation_id)
    messages = await limited(
        db,
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at),
        limit,
        cursor,
    )
    return {"conversation": out(item), "messages": messages}


@app.delete("/api/v1/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_session)
):
    item = await require(db, Conversation, conversation_id)
    messages = (
        await db.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
        )
    ).all()
    if any(
        m.run_id
        and (r := await db.get(Run, m.run_id))
        and r.status in ("queued", "running")
        for m in messages
    ):
        raise HTTPException(409, "Conversation has active work")
    for message in messages:
        await db.delete(message)
    await db.delete(item)
    await db.commit()


@app.post("/api/v1/conversations/{conversation_id}/messages", status_code=202)
async def send_message(
    conversation_id: str, body: MessageCreate, db: AsyncSession = Depends(get_session)
):
    convo = await require(db, Conversation, conversation_id)
    snap = await require(db, Snapshot, body.snapshot_id)
    if snap.connection_id != convo.connection_id:
        raise HTTPException(422, "Snapshot does not belong to conversation connection")
    run = Run(
        kind="chat",
        input={
            "conversation_id": conversation_id,
            "snapshot_id": snap.id,
            "question": body.text,
            "object_ids": body.object_ids,
            "mode": body.mode,
        },
    )
    db.add(run)
    await db.flush()
    message = Message(
        conversation_id=conversation_id,
        role="user",
        content=body.text,
        snapshot_id=snap.id,
        object_ids=body.object_ids,
        run_id=run.id,
    )
    db.add(message)
    await db.commit()
    await manager.submit(run.id)
    return {
        "run_id": run.id,
        "message_id": message.id,
        "events_url": f"/api/v1/runs/{run.id}/events",
    }


@app.post("/api/v1/runs", status_code=202)
async def create_run(body: RunCreate, db: AsyncSession = Depends(get_session)):
    data = body.model_dump(exclude_none=True)
    if body.connection_id:
        await require(db, Connection, body.connection_id)
    if body.snapshot_id:
        snap = await require(db, Snapshot, body.snapshot_id)
        if body.connection_id and body.connection_id != snap.connection_id:
            raise HTTPException(422, "Connection and snapshot do not match")
    if body.kind == "scan" and not body.connection_id:
        raise HTTPException(422, "Scan requires connection_id")
    if (
        body.kind
        not in (
            "scan",
            "query_plan",
            "query_execute",
            "schema_diff",
            "import_schema",
            "workload_analysis",
        )
        and not body.snapshot_id
    ):
        raise HTTPException(422, "Operation requires snapshot_id")
    if body.kind in ("query_plan", "query_execute") and (
        not body.connection_id or not body.sql
    ):
        raise HTTPException(422, "Query operation requires connection_id and sql")
    if body.kind == "workload_analysis" and not body.connection_id:
        raise HTTPException(422, "Workload analysis requires connection_id")
    if body.kind == "import_schema" and not body.document_id:
        raise HTTPException(422, "Schema import requires document_id")
    if body.kind == "schema_diff" and (
        not body.before_snapshot_id or not body.after_snapshot_id
    ):
        raise HTTPException(
            422, "Schema diff requires before_snapshot_id and after_snapshot_id"
        )
    if (
        body.kind in ("normalization", "experiment", "generate_demo_data")
        and not body.object_id
    ):
        raise HTTPException(422, f"{body.kind} requires object_id")
    if body.kind == "experiment" and not body.column:
        raise HTTPException(422, "Experiment requires column")
    if body.kind == "profile" and not body.object_ids:
        raise HTTPException(422, "Profiling requires object_ids")
    if body.kind == "query_draft" and not body.question:
        raise HTTPException(422, "Query draft requires question")
    if body.kind == "analyze_context" and not body.document_ids:
        raise HTTPException(422, "Context analysis requires document_ids")
    run = Run(kind=body.kind, input=data)
    db.add(run)
    await db.flush()
    audit(db, "run.submitted", "run", run.id, {"kind": body.kind})
    await db.commit()
    await manager.submit(run.id)
    return {
        "run_id": run.id,
        "status": "queued",
        "status_url": f"/api/v1/runs/{run.id}",
        "events_url": f"/api/v1/runs/{run.id}/events",
    }


@app.get("/api/v1/runs")
async def list_runs(
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Run)
    if kind:
        stmt = stmt.where(Run.kind == kind)
    if status:
        stmt = stmt.where(Run.status == status)
    return await limited(db, stmt.order_by(Run.created_at.desc()), limit, cursor)


@app.get("/api/v1/jev/runs")
async def jev_runs(limit: int = Query(12, ge=1, le=25), db: AsyncSession = Depends(get_session)):
    require_jev()
    runs = (await db.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit))).all()
    items = [(run.id, {"kind": run.kind, "status": run.status, "stage": run.stage,
                      "created_at": run.created_at.isoformat(),
                      "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                      "result_keys": sorted(run.result or {})}) for run in runs]
    result = await jev_batch(items, RUN_QUESTIONS)
    result.sort(key=lambda item: item.get("decisions", {}).get("attention", {}).get("value", -1), reverse=True)
    return {"items": result}


@app.get("/api/v1/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_session)):
    return out(await require(db, Run, run_id))


@app.post("/api/v1/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_session)):
    run = await require(db, Run, run_id)
    if run.status == "queued":
        run.status = "cancelled"
        run.stage = "cancelled"
        run.finished_at = utcnow()
        await db.commit()
        await add_event(run_id, "run.cancelled", {})
    elif run.status == "running":
        run.status = "cancelling"
        await db.commit()
        manager.cancel(run_id)
    return out(run)


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(
    run_id: str, request: Request, db: AsyncSession = Depends(get_session)
):
    await require(db, Run, run_id)
    try:
        last = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        last = 0

    async def stream():
        nonlocal last
        while not await request.is_disconnected():
            async with SessionLocal() as session:
                events = (
                    await session.scalars(
                        select(RunEvent)
                        .where(RunEvent.run_id == run_id, RunEvent.sequence > last)
                        .order_by(RunEvent.sequence)
                    )
                ).all()
                run = await session.get(Run, run_id)
            for event in events:
                last = event.sequence
                yield {
                    "id": str(event.sequence),
                    "event": event.event_type,
                    "data": json.dumps(event.payload, default=str),
                }
            if run.status in ("succeeded", "failed", "cancelled", "interrupted"):
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(stream())


@app.post("/api/v1/context-documents", status_code=201)
async def create_context_document(
    connection_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    await require(db, Connection, connection_id)
    data = await file.read(1_000_001)
    if len(data) > 1_000_000:
        raise HTTPException(413, "Maximum file size is 1 MB")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Only UTF-8 text files are supported") from exc
    if (
        not (file.filename or "")
        .lower()
        .endswith((".sql", ".md", ".txt", ".json", ".py", ".ts"))
    ):
        raise HTTPException(422, "Unsupported file type")
    item = ContextDocument(
        connection_id=connection_id,
        filename=(file.filename or "document")[:255],
        kind=(file.filename or "").rsplit(".", 1)[-1],
        content=content,
    )
    db.add(item)
    await db.commit()
    return out(item)


@app.get("/api/v1/context-documents")
async def list_context_documents(
    connection_id: str, db: AsyncSession = Depends(get_session)
):
    items = (
        await db.scalars(
            select(ContextDocument).where(
                ContextDocument.connection_id == connection_id
            )
        )
    ).all()
    return [
        {"id": x.id, "filename": x.filename, "kind": x.kind, "created_at": x.created_at}
        for x in items
    ]


@app.get("/api/v1/context-documents/{document_id}")
async def get_context_document(
    document_id: str, db: AsyncSession = Depends(get_session)
):
    return out(await require(db, ContextDocument, document_id))


@app.delete("/api/v1/context-documents/{document_id}", status_code=204)
async def delete_context_document(
    document_id: str, db: AsyncSession = Depends(get_session)
):
    await db.delete(await require(db, ContextDocument, document_id))
    await db.commit()
