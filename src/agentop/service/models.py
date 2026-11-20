"""Request and response models for /api/v2. The web client is generated from these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Health(BaseModel):
    """Only whether the service is up. It needs no token, so it says nothing else."""

    status: str


class ServiceInfo(BaseModel):
    version: str
    api_version: int = Field(description="Revision of /api/v2. A client built for a lower one should be updated")
    name: str
    commit: str | None
    uptime_seconds: int
    # When this process started serving. A restart changes it, even one that keeps the PID.
    started_at: float | None = None
    pid: int
    tmux_version: str | None
    manager: str
    capabilities: dict[str, bool]
    install_kind: str
    tls: bool
    plugins: int = Field(description="How many plugins the service found, in any state")
    ui_build: str | None = Field(
        None, description="The entry script of the app the service serves now. A page with another one is out of date"
    )


class PluginInfo(BaseModel):
    id: str
    name: str
    version: str
    description: str
    state: Literal["enabled", "disabled", "failed", "incompatible"]
    error: str | None
    ui: list[str] = Field(description="The UI slots the plugin fills")


class PatchPlugin(BaseModel):
    enabled: bool


class Accepted(BaseModel):
    status: str


class UpdateRun(BaseModel):
    """The latest update, as update.sh reports it."""

    state: Literal["running", "done", "failed", "rolled_back", "rollback_failed"]
    step: str | None = None
    message: str | None = None
    from_commit: str | None = None
    to_commit: str | None = None
    updated_at: int | None = None


class UpdateInfo(BaseModel):
    current: str | None
    # The release tag when following releases; else the branch's newest commit.
    upstream: str | None
    behind: int | None
    check_error: str | None
    update_from: Literal["main", "release"] = "main"
    running: bool
    last_run: UpdateRun | None


class VersionInfo(BaseModel):
    """What the service runs and what Update would bring, in one form: X.Y.Z following releases, else a commit."""

    update_from: Literal["main", "release"]
    current: str | None
    latest: str | None = Field(description="Equal to current when nothing is newer")
    ui_build: str | None = Field(description="The entry script of the app the service serves now")
    checked_at: float
    check_error: str | None


class HistoryItem(BaseModel):
    """A past session agentop started, still resumable."""

    id: str = Field(description="Its record in the registry: the agentop session it was last")
    project: str = "default"
    tool: str
    session_id: str = Field(description="The agent tool's own ID for it")
    name: str
    title: str | None = None
    description: str = ""
    cwd: str
    created_at: float | None = None
    ended_at: float | None = None
    last_active: float = Field(description="When the tool last wrote its transcript")


class HistoryListing(BaseModel):
    items: list[HistoryItem]
    window_seconds: float = Field(description="Only sessions used this recently are listed")
    active: int = Field(description="Sessions agentop runs now, not counting a plugin's own")
    max_active: int = Field(description="How many may be active at once; resuming waits for one to close")


class HistoryStats(BaseModel):
    """What a past session's transcript holds."""

    prompts: int = Field(description="What the user typed, in the main conversation (not a subagent's)")
    replies: int = Field(description="The agent's text replies in the main conversation")
    token_usage: TokenUsage | None = Field(None, description="Summed over every file, subagents included; None if the tool records none")
    files: int = Field(description="Transcript files: the conversation log and what the tool keeps beside it")
    bytes: int = Field(description="Their size on disk")
    truncated: bool = Field(description="More files than the listing limit; only those counted")
    archived: bool = Field(description="The tool deleted its own files; these are the Memory archive's copy")


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
    # The directory's path inside the repository, ending in "/", or "" at its top.
    prefix: str = ""


class TranscriptFile(BaseModel):
    path: str
    size: int
    modified: float


class TranscriptListing(BaseModel):
    root: str | None
    files: list[TranscriptFile]
    truncated: bool
    # The tool deleted its own files; these are the Memory archive's copy.
    archived: bool = False


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
    # The project a session agentop started is in; None for one started elsewhere.
    project: str | None = None
    # Set by the plugin that started it: a dialogue's agents are watched only, and don't count towards the limit.
    readonly: bool = False
    counted: bool = True
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


class ProjectModel(BaseModel):
    name: str
    owner: str | None = None
    folder: str
    created_at: float | None = None


class ResumeRef(BaseModel):
    session_id: str


class CreateSession(BaseModel):
    tool: str
    cwd: str = Field(min_length=1)
    name: str | None = None
    resume: ResumeRef | None = None
    project: str | None = None


class SendPrompt(BaseModel):
    target: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=100_000)
    project: str | None = None


class SentPrompt(BaseModel):
    name: str
    tmux: str


class PatchSession(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class TicketRequest(BaseModel):
    purpose: Literal["events", "terminal"]
    session_id: str | None = None


class Ticket(BaseModel):
    ticket: str
    expires_in: int


class AddClient(BaseModel):
    name: str = Field(default="Client", min_length=1, max_length=80)
    # Shorter than the service's own lifetime, for a client that needs only a moment (the CLI).
    ttl_seconds: int | None = Field(default=None, ge=1)


class LoginMethod(BaseModel):
    # "token" for the service token, else the id of the plugin that offers it.
    id: str
    name: str


class ClientToken(BaseModel):
    client_id: str
    token: str
    # Seconds until this token stops working, unless renewed.
    expires_in: int
    # The service's lifetime for a token. The app renews once a token is a twelfth of this old.
    ttl: int


class ClientInfo(BaseModel):
    id: str
    name: str
    created_at: float
    last_seen: float
    expires_at: float
    ends_at: float | None
    # The client making this request.
    current: bool
