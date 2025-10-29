from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator


class RepositoryRequest(BaseModel):
    repo_url: str
    commit_id: str
    project_name: Optional[str] = None
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
    status: str
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

    class Config:
        orm_mode = True


class StorageUsageInfo(BaseModel):
    filesystem: str
    size_kb: int
    used_kb: int
    available_kb: int
    use_percent: str
    mountpoint: str


class OpenGrokPodStatus(BaseModel):
    pod_name: str
    pod_status: str
    pod_ip: Optional[str] = None
    node_name: Optional[str] = None
    storage_usage: list[StorageUsageInfo]