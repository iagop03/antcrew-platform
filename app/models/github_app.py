"""GitHub App installation model."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class GitHubInstallation(SQLModel, table=True):
    __tablename__ = "github_installation"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    installation_id: int = Field(index=True)  # GitHub's numeric installation ID
    account_login: str  # org/user name where the app is installed
    account_type: str = "Organization"  # "Organization" | "User"
    # Repositories the installation has access to (JSON list of full_name strings)
    # Empty list = all repos
    repo_allowlist: str = "[]"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
