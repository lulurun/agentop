"""Request and response models for /api/v2. The web client is generated from these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Health(BaseModel):
    status: str
    version: str
    api_version: int
    instance_id: str
    name: str


class ServiceInfo(Health):
    commit: str | None
    uptime_seconds: int
    pid: int
    tmux_version: str | None
    manager: str
    capabilities: dict[str, bool]
    install_kind: str
    tls: bool


class ToolInfo(BaseModel):
    name: str
    installed: bool
    supports_resume: bool


class DirListing(BaseModel):
    path: str
    parent: str | None
    dirs: list[str]


class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    is_link: bool


class FileListing(BaseModel):
    root: str
    path: str
    entries: list[FileEntry]
    truncated: bool


class ChangedFile(BaseModel):
    path: str
    status: str


class GitChanges(BaseModel):
    is_repo: bool
    branch: str | None
    ahead: int
    behind: int
    files: list[ChangedFile]
    truncated: bool


class TmuxInfo(BaseModel):
    session: str
    clients: int


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class SessionModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    display_name: str
    description: str
    tool: str | None
    status: Literal["starting", "running", "exited", "failed"]
    step: Literal["creating", "launching", "waiting", "confirming"] | None
    error: str | None
    managed: bool
    attachable: bool
    cwd: str
    created_at: float | None
    pid: int | None
    runtime_seconds: int | None
    memory_mb: float | None
    tmux: TmuxInfo | None
    ai_title: str | None = None
    session_id: str | None = None
    token_usage: TokenUsage | None = None
    bridge_url: str | None = None


class ResumeRef(BaseModel):
    session_id: str


class CreateSession(BaseModel):
    tool: str
    cwd: str = Field(min_length=1)
    name: str | None = None
    resume: ResumeRef | None = None


class PatchSession(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class TicketRequest(BaseModel):
    purpose: Literal["events", "terminal"]
    session_id: str | None = None


class Ticket(BaseModel):
    ticket: str
    expires_in: int
