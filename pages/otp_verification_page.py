"""BLS e-posta OTP / dogrulama kodu ekrani (step 0.5 civari akis).

HTML omegi projede `step0.5.html` ana sayfa olabilir; gercek OTP genelde ayri bir
modal veya form satirinda `input` ile gelir. Lokatorler birden fazla stratejiyi `or_`
ile birlestirir.
"""

from __future__ import annotations

import re

from playwright.async_api import Locator, Page


class BLSOtpVerificationPage:
    """OTP tek alan veya kisa kod alani; Verify / Dogrula butonu."""

    def __init__(self, page: Page) -> None:
        self._page = page

    def otp_code_input(self) -> Locator:
        """Etiket + maxlength/inputmode ile gorunur kod alani."""
        label_pat = re.compile(
            r"otp|one-?time|doğrulama|dogrulama|verification|kod|code|pin",
            re.I,
        )
        by_label = self._page.get_by_label(label_pat)
        max6 = self._page.locator('input[maxlength="6"]:visible')
        numeric = self._page.locator('input[inputmode="numeric"]:visible')
        return by_label.or_(max6).or_(numeric).first

    def verify_submit_button(self) -> Locator:
        by_role = self._page.get_by_role(
            "button",
            name=re.compile(
                r"verify|doğrula|dogrula|submit|gönder|gonder|confirm|onay",
                re.I,
            ),
        )
        by_id = self._page.locator("#btnVerify")
        return by_role.or_(by_id).first

    def apparent_error_locator(self) -> Locator:
        """Yanlis/suresi dolmus OTP mesajlari (genel ARIA / Bootstrap)."""
        return self._page.get_by_role("alert").or_(
            self._page.locator(".text-danger:visible, .invalid-feedback:visible")
        )
