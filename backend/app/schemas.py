from datetime import datetime
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field, validator


class RepositoryStatus(str, Enum):
    """Enumeration for repository processing statuses."""
    PENDING = "PENDING"
    POD_CREATING = "POD_CREATING"
    CLONING = "CLONING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETION_FAILED = "DELETION_FAILED"
    UNKNOWN_CLEANUP = "UNKNOWN_CLEANUP"


class RepositoryRequest(BaseModel):
    repo_url: str
    commit_id: str
    project_name: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
        description="Optional custom project name. Must consist of lowercase alphanumeric characters or '-', and must start and end with an alphanumeric character.",
    )
    clone_single_branch: bool = False
    clone_recursive: bool = False
    # 0 means indefinite.
    retention_days: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of days to retain the repository. 0 means indefinite.",
    )
    auto_sync_enabled: bool = False
    auto_sync_schedule: Optional[str] = Field(
        default=None,
        pattern=r"^(2[0-3]|[01]?[0-9]):([0-5]?[0-9])$",
        description="Daily sync time in 'HH:MM' format (UTC). Required if auto_sync_enabled is true.",
    )

    @validator('auto_sync_schedule', always=True)
    def schedule_required_if_enabled(cls, v, values):
        if values.get('auto_sync_enabled') and v is None:
            raise ValueError('auto_sync_schedule is required when auto_sync_enabled is true')
        if not values.get('auto_sync_enabled'):
            return None # If disabled, always set schedule to None, ignoring any passed value.
        return v

    @validator('commit_id')
    def validate_commit_id(cls, v):
        if not v:
            raise ValueError('commit_id cannot be empty')
        # Rules based on git-check-ref-format
        # 1. It must not contain spaces or invalid characters.
        if any(char in v for char in ' ~^:?*[\\]'):
            raise ValueError('cannot contain spaces or special characters: ~^:?*[]\\')
        # 2. It must not contain ".."
        if '..' in v:
            raise ValueError('cannot contain ".."')
        # 3. It must not start or end with a "/"
        if v.startswith('/') or v.endswith('/'):
            raise ValueError('cannot start or end with "/"')
        return v


class RepositoryExpirationUpdateRequest(BaseModel):
    # 0 means indefinite.
    retention_days: int = Field(
        ge=0,
        description="New number of days to retain the repository from now. 0 means indefinite.",
    )


class RepositoryAutoSyncUpdateRequest(BaseModel):
    auto_sync_enabled: bool
    auto_sync_schedule: Optional[str] = Field(
        default=None,
        pattern=r"^(2[0-3]|[01]?[0-9]):([0-5]?[0-9])$",
        description="Daily sync time in 'HH:MM' format (UTC). Required if auto_sync_enabled is true.",
    )


class JobLogs(BaseModel):
    logs: str


class AppConfig(BaseModel):
    opengrok_base_url: Optional[str] = None


class RepositoryInfo(BaseModel):
    id: int
    repo_url: str
    commit_id: str
    status: RepositoryStatus
    job_name: str
    pvc_path: str
    created_at: datetime
    updated_at: datetime
    expired_at: Optional[datetime] = None  # Expiration timestamp
    last_synced_at: Optional[datetime] = None
    clone_single_branch: bool
    clone_recursive: bool
    auto_sync_enabled: bool
    auto_sync_schedule: Optional[str] = None
    task_log: Optional[str] = None

    class Config:
        orm_mode = True


class StorageUsageInfo(BaseModel):
    filesystem: str
    size_kb: int
    used_kb: int
    available_kb: int
    use_percent: str
    mountpoint: str


class OpenGrokDeploymentStatus(BaseModel):
    name: str
    replicas: int
    ready_replicas: int
    available_replicas: int
    unavailable_replicas: int
    updated_replicas: int


class OpenGrokPodStatus(BaseModel):
    pod_name: str
    pod_status: str
    pod_ip: Optional[str] = None
    node_name: Optional[str] = None
    cpu_usage: Optional[str] = None
    memory_usage: Optional[str] = None
    storage_usage: list[StorageUsageInfo]


class OpenGrokStatusResponse(BaseModel):
    deployment_status: Optional[OpenGrokDeploymentStatus] = None
    pod_statuses: list[OpenGrokPodStatus]


# --- Export/Import Schemas ---

class RepositoryExport(BaseModel):
    """Schema for a single repository in the export format."""
    repo_url: str
    commit_id: str
    pvc_path: str
    clone_single_branch: bool
    clone_recursive: bool
    retention_days: Optional[int] = None  # Calculated from expired_at, or None for indefinite
    auto_sync_enabled: bool
    auto_sync_schedule: Optional[str] = None


class RepositoriesExportResponse(BaseModel):
    """Response schema for the export endpoint."""
    version: str = "1.0"
    exported_at: datetime
    repositories: list[RepositoryExport]


class RepositoriesImportRequest(BaseModel):
    """Request schema for the import endpoint."""
    repositories: list[RepositoryExport]


class RepositoryImportResult(BaseModel):
    """Result for a single repository import attempt."""
    pvc_path: str
    status: str  # "created", "skipped", "error"
    message: Optional[str] = None


class RepositoriesImportResponse(BaseModel):
    """Response schema for the import endpoint."""
    total: int
    created: int
    skipped: int
    errors: int
    results: list[RepositoryImportResult]