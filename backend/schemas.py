from __future__ import annotations

from pydantic import BaseModel, Field


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
