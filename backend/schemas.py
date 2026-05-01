from __future__ import annotations

from pydantic import BaseModel, Field


class ProxySummary(BaseModel):
    id: int
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    note: str = ""
    fail_count: int = 0
    lock_until: str = ""


class ProxyRead(BaseModel):
    id: int
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    note: str = ""
    assigned_profile_id: int | None = None
    assigned_profile_label: str | None = None
    is_assigned: bool
    fail_count: int
    lock_until: str = ""


class ProxyCreate(BaseModel):
    scheme: str = Field(default="http", max_length=16)
    host: str = Field(..., max_length=512)
    port: int = Field(ge=1, le=65535)
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=256)
    note: str = Field(default="", max_length=512)


class ProxyUpdate(BaseModel):
    scheme: str | None = Field(default=None, max_length=16)
    host: str | None = Field(default=None, max_length=512)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=512)


class ProxyBulkImport(BaseModel):
    text: str = Field(..., description="Satir satir proxy listesi")


class AssignProxyBody(BaseModel):
    proxy_id: int | None = Field(
        None, description="Atanacak proxy id; null ise profilden proxy kaldirilir"
    )


class ProfileCreate(BaseModel):
    label: str = Field(default="varsayilan", max_length=120)
    email: str = Field(..., max_length=320)
    password: str = Field(default="", max_length=512)
    login_url: str = Field(
        default="https://turkey.blsspainglobal.com/Global/Account/LogIn",
        max_length=2000,
    )


class ProfileUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=512)
    login_url: str | None = Field(default=None, max_length=2000)


class ProfileRead(BaseModel):
    id: int
    label: str
    email: str
    password: str
    login_url: str
    is_active: bool
    run_count: int = 0
    proxy: ProxySummary | None = None


class RotateAssignResult(BaseModel):
    assigned_pairs: int
    profiles_without_proxy: int
