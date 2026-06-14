"""Dialogue status enums shared across actor, model, and orchestrator."""

from enum import StrEnum


class ReceiveStatus(StrEnum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    DELIMITER_NOT_FOUND_WILL_RETRY = "delimiter_not_found"
    DELIMITER_NOT_FOUND_RETRIES_EXHAUSTED = "delimiter_exhausted"


class DialogueStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERROR = "error"
    AGENT_REPEATEDLY_MISSING_DELIMITER = "agent_missing_delimiter"
