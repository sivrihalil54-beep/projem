"""Panel -> bot: kategorize musteri JSON'unu `CustomerRead` uzerinden uret.

Playwright ornegi (JS) ile hizalı alan adlari:
  customer.location.province_label  -> getByLabel('Select Province').selectOption({ label })
  customer.visa.category_radio_name -> getByRole('radio', { name })
  customer.playwright_hints.otp_*   -> OTP alani bekleme
"""

from __future__ import annotations

from backend.schemas import (
    CustomerRead,
    PanelCustomerBotBundle,
    PanelLocation,
    PanelPersonal,
    PanelPlaywrightHints,
    PanelVisa,
)

_BLS_OFFICE_NAME: dict[str, str] = {
    "6888": "Ankara",
    "6889": "Antalya",
    "6890": "Baku - Azerbaijan",
    "6891": "Gaziantep",
    "6892": "Istanbul",
    "6893": "Izmir",
}

_BLS_CATEGORY_RADIO: dict[str, str] = {
    "CATEGORY_NORMAL": "Normal",
    "CATEGORY_PREMIUM": "Premium",
    "PRIME_TIME": "Prime Time",
    "DOORSTEP_SERVICE": "Doorstep Service",
}

_PANEL_ALIAS: dict[str, str] = {
    "CATEGORY_NORMAL": "Normal",
    "CATEGORY_PREMIUM": "Premium",
    "PRIME_TIME": "VIP",
    "DOORSTEP_SERVICE": "Doorstep Service",
}

# Schengen alt tipler (visaSubId); panel: Turistik / Ticari / Aile
_TOURIST_SUBIDS = frozenset(
    {"7298", "7299", "7300", "7301", "7302", "7303"}
)
_BUSINESS_SUBIDS = frozenset({"7244", "7245", "7246", "7247", "7248", "7249"})
_FAMILY_SUBIDS = frozenset({"7271", "7272", "7273"})


def _infer_simplified_kind(visa_subtype_id: str) -> str:
    s = (visa_subtype_id or "").strip()
    if s in _TOURIST_SUBIDS:
        return "tourist"
    if s in _BUSINESS_SUBIDS:
        return "business"
    if s in _FAMILY_SUBIDS:
        return "family"
    return "unknown"


def customer_row_to_bot_bundle(customer: CustomerRead) -> PanelCustomerBotBundle:
    if customer.profile_id is None:
        raise ValueError("Panel bundle icin profile_id zorunlu")

    cat = (customer.appointment_category or "CATEGORY_NORMAL").strip()
    radio_name = _BLS_CATEGORY_RADIO.get(cat, "Normal")
    panel_alias = _PANEL_ALIAS.get(cat, "Normal")

    office = (customer.bls_office_code or "").strip()
    office_name = _BLS_OFFICE_NAME.get(office, office or "")

    kind = _infer_simplified_kind(customer.visa_type)

    return PanelCustomerBotBundle(
        profile_id=int(customer.profile_id),
        customer_id=int(customer.id),
        personal=PanelPersonal(
            first_name=customer.first_name,
            last_name=customer.last_name,
            tc_kimlik_no=customer.tc_kimlik_no,
            passport_no=customer.passport_no,
            birth_date=customer.birth_date,
        ),
        location=PanelLocation(
            city=(customer.city or "").strip(),
            province_label=(customer.city or "").strip(),
            bls_jurisdiction_id=(customer.bls_jurisdiction_id or "").strip(),
            application_center_id=office,
            application_center_name=office_name,
            notes=customer.notes or "",
        ),
        visa=PanelVisa(
            category_code=cat,
            category_radio_name=radio_name,
            panel_category=panel_alias,
            simplified_kind=kind,
            bls_visa_type_id=(customer.bls_visa_type_id or "").strip(),
            visa_subtype_id=(customer.visa_type or "").strip(),
        ),
        playwright_hints=PanelPlaywrightHints(),
    )
