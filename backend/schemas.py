from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from utils.email_normalize import normalize_email

from backend.bls_step2_data import (
    BLS_CATEGORY_CODES,
    BLS_JURISDICTION_IDS,
    BLS_LOCATION_IDS,
    BLS_VISA_SUBTYPE_IDS,
    BLS_VISA_TYPE_IDS,
)


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
    is_active: bool = True
    last_used_at: str = ""


class ProxyCreate(BaseModel):
    scheme: str = Field(default="http", max_length=16)
    host: str = Field(..., min_length=1, max_length=512)
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


class ProxyBulkDelete(BaseModel):
    """delete_all=true ise tum havuz silinir; aksi halde ids zorunludur."""

    ids: list[int] | None = Field(
        default=None,
        description="Silinecek proxy id listesi",
    )
    delete_all: bool = Field(
        default=False,
        description="True ise havuzda kalan tum kayitlari siler",
    )


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
    gmail_app_password: str | None = Field(
        default=None,
        max_length=256,
        description="Gmail IMAP icin uygulama sifresi; bos veya None ise veritabaninda NULL",
    )

    @field_validator("email")
    @classmethod
    def validate_email_normalized(cls, v: str) -> str:
        s = normalize_email(v)
        if not s:
            raise ValueError("E-posta bos olamaz.")
        local, _, domain = s.partition("@")
        if not local or not domain or "." not in domain:
            raise ValueError(
                "E-posta gecerli bir adres formunda olmalidir (orn. kullanici@alan.com)."
            )
        if len(s) > 320:
            raise ValueError("E-posta cok uzun (en fazla 320 karakter).")
        return s


class ProfileUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=512)
    login_url: str | None = Field(default=None, max_length=2000)
    gmail_app_password: str | None = Field(
        default=None,
        max_length=256,
        description="Yeni uygulama sifresi; birakilirsa (None) degismez clear_gmail_app_password ile birlikte kullanmayin",
    )
    clear_gmail_app_password: bool = Field(
        default=False,
        description="True ise gmail_app_password alani silinir (NULL)",
    )

    @field_validator("email")
    @classmethod
    def validate_email_normalized_opt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = normalize_email(v)
        if not s:
            raise ValueError("E-posta bos birakilamaz; degistirmeyecekseniz alani eski degerle gonderin.")
        local, _, domain = s.partition("@")
        if not local or not domain or "." not in domain:
            raise ValueError(
                "E-posta gecerli bir adres formunda olmalidir (orn. kullanici@alan.com)."
            )
        return s


class ProfileRead(BaseModel):
    id: int
    label: str
    email: str
    password: str
    login_url: str
    gmail_app_password: str | None = None
    is_active: bool
    run_count: int = 0
    last_error: str = ""
    last_error_at: str = ""
    proxy: ProxySummary | None = None


class ProfileLastErrorBody(BaseModel):
    """Bot run_login_step teşhisi — panel Son Hata alanı (bos= temizle)."""

    message: str = Field(default="", max_length=2000)


class StartBotRequest(BaseModel):
    """Panelden bot baslatirken istege bagli alanlar."""

    skip_otp: bool = Field(
        default=False,
        description="True ise --no-otp: yalnizca giris adimi, Gmail OTP atlanir",
    )


class RotateAssignResult(BaseModel):
    assigned_pairs: int
    profiles_without_proxy: int


class ProfileRotateAssignResult(BaseModel):
    """Tek profil icin havuzdan en uygun proxy secimi."""

    profile_id: int
    proxy_id: int | None = None
    scheme: str = ""
    host: str = ""
    port: int = 0
    message: str = ""


TC_KIMLIK_RE = re.compile(r"^[1-9]\d{10}$")
PASSPORT_RE = re.compile(r"^[A-Za-z0-9]{6,20}$")


class CustomerBase(BaseModel):
    profile_id: int | None = Field(
        default=None,
        description="Bagli bot hesabi (bot baslat icin)",
    )
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    tc_kimlik_no: str = Field(default="", max_length=11)
    passport_no: str = Field(default="", max_length=24)
    birth_date: str = Field(
        default="",
        max_length=32,
        description="YYYY-MM-DD",
    )
    city: str = Field(
        default="",
        max_length=128,
        description="BLS jurisdiction Name (görüntüleme; step2 HTML)",
    )
    bls_jurisdiction_id: str = Field(
        default="",
        max_length=64,
        description="jurisdictionData Id (UUID)",
    )
    bls_office_code: str = Field(
        default="",
        max_length=16,
        description="locationData Id: 6888=Ankara, 6892=Istanbul, ...",
    )
    appointment_category: str = Field(
        default="CATEGORY_NORMAL",
        max_length=48,
        description="categoryData Code (DOORSTEP_SERVICE, CATEGORY_NORMAL, ...)",
    )
    bls_visa_type_id: str = Field(
        default="",
        max_length=16,
        description="visaIdData Id (ust vize sinifi)",
    )
    visa_type: str = Field(
        default="",
        max_length=16,
        description="visasubIdData Id (alt vize / seçilen satır)",
    )
    live_status: str = Field(default="Hazır", max_length=256)
    notes: str = Field(default="", max_length=2000)

    @field_validator("tc_kimlik_no")
    @classmethod
    def validate_tc(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        if not TC_KIMLIK_RE.fullmatch(s):
            raise ValueError(
                "TC Kimlik No 11 haneli olmali ve ilk rakam 0 olamaz (regex: [1-9]\\d{10})"
            )
        return s

    @field_validator("passport_no")
    @classmethod
    def validate_passport(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        if not PASSPORT_RE.fullmatch(s):
            raise ValueError(
                "Pasaport No 6-20 karakter, yalnizca harf ve rakam (A-Za-z0-9)"
            )
        return s

    @field_validator("bls_jurisdiction_id")
    @classmethod
    def validate_jurisdiction(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        if s not in BLS_JURISDICTION_IDS:
            raise ValueError("Gecersiz BLS jurisdiction Id")
        return s

    @field_validator("bls_office_code")
    @classmethod
    def validate_location(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        if s not in BLS_LOCATION_IDS:
            raise ValueError(
                "Gecersiz BLS basvuru merkezi Id (locationData): "
                + ", ".join(sorted(BLS_LOCATION_IDS))
            )
        return s

    @field_validator("appointment_category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        s = (v or "").strip() or "CATEGORY_NORMAL"
        if s not in BLS_CATEGORY_CODES:
            raise ValueError(
                "Randevu kategorisi BLS categoryData Code olmalidir: "
                + ", ".join(sorted(BLS_CATEGORY_CODES))
            )
        return s

    @field_validator("bls_visa_type_id")
    @classmethod
    def validate_visa_type_id(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        if s not in BLS_VISA_TYPE_IDS:
            raise ValueError("Gecersiz BLS visaIdData Id")
        return s

    @field_validator("visa_type")
    @classmethod
    def validate_visa_subtype(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        if s not in BLS_VISA_SUBTYPE_IDS:
            raise ValueError("Gecersiz BLS visasubIdData Id")
        return s


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    profile_id: int | None = None
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    tc_kimlik_no: str | None = Field(default=None, max_length=11)
    passport_no: str | None = Field(default=None, max_length=24)
    birth_date: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=128)
    bls_jurisdiction_id: str | None = Field(default=None, max_length=64)
    bls_office_code: str | None = Field(default=None, max_length=16)
    appointment_category: str | None = Field(default=None, max_length=48)
    bls_visa_type_id: str | None = Field(default=None, max_length=16)
    visa_type: str | None = Field(default=None, max_length=16)
    live_status: str | None = Field(default=None, max_length=256)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("tc_kimlik_no")
    @classmethod
    def validate_tc(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return ""
        if not TC_KIMLIK_RE.fullmatch(s):
            raise ValueError(
                "TC Kimlik No 11 haneli olmali ve ilk rakam 0 olamaz (regex: [1-9]\\d{10})"
            )
        return s

    @field_validator("passport_no")
    @classmethod
    def validate_passport(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return ""
        if not PASSPORT_RE.fullmatch(s):
            raise ValueError(
                "Pasaport No 6-20 karakter, yalnizca harf ve rakam (A-Za-z0-9)"
            )
        return s

    @field_validator("bls_jurisdiction_id")
    @classmethod
    def validate_jurisdiction(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return ""
        if s not in BLS_JURISDICTION_IDS:
            raise ValueError("Gecersiz BLS jurisdiction Id")
        return s

    @field_validator("bls_office_code")
    @classmethod
    def validate_location(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return ""
        if s not in BLS_LOCATION_IDS:
            raise ValueError("Gecersiz BLS basvuru merkezi Id")
        return s

    @field_validator("appointment_category")
    @classmethod
    def validate_category(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip() or "CATEGORY_NORMAL"
        if s not in BLS_CATEGORY_CODES:
            raise ValueError("Randevu kategorisi BLS categoryData Code degil")
        return s

    @field_validator("bls_visa_type_id")
    @classmethod
    def validate_visa_type_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return ""
        if s not in BLS_VISA_TYPE_IDS:
            raise ValueError("Gecersiz BLS visaIdData Id")
        return s

    @field_validator("visa_type")
    @classmethod
    def validate_visa_subtype(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return ""
        if s not in BLS_VISA_SUBTYPE_IDS:
            raise ValueError("Gecersiz BLS visasubIdData Id")
        return s


class CustomerRead(CustomerBase):
    id: int
    created_at: str = ""
    updated_at: str = ""


class PanelPersonal(BaseModel):
    first_name: str
    last_name: str
    tc_kimlik_no: str
    passport_no: str
    birth_date: str


class PanelLocation(BaseModel):
    """BLS il / jurisdiction — Playwright: Select Province benzeri etiketler."""

    city: str
    province_label: str
    bls_jurisdiction_id: str
    application_center_id: str
    application_center_name: str
    notes: str


class PanelVisa(BaseModel):
    category_code: str
    category_radio_name: str
    panel_category: str
    simplified_kind: str
    bls_visa_type_id: str
    visa_subtype_id: str


class PanelPlaywrightHints(BaseModel):
    province_select_label: str = "Select Province"
    otp_placeholder: str = "Enter OTP"
    otp_visible_timeout_ms: int = 90_000


class PanelCustomerBotBundle(BaseModel):
    """
    Standart panel ciktisi — bot / harici Playwright bu JSON ile `getByLabel` / `getByRole`
    eslesmelerini yapar. Kaynak: GET /api/customers/by-profile/{profile_id}
    """

    profile_id: int
    customer_id: int
    personal: PanelPersonal
    location: PanelLocation
    visa: PanelVisa
    playwright_hints: PanelPlaywrightHints = Field(
        default_factory=PanelPlaywrightHints
    )


class CustomerLiveStatusBody(BaseModel):
    live_status: str = Field(..., min_length=1, max_length=256)
