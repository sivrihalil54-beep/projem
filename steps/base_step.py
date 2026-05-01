"""Tum adim siniflari icin ortak temel.

Yalnizca gercek aksiyonlar (ag, dosya, tarayici vb.) baslamadan once ve bittikten sonra
`action_start` / `action_done` cagirin. Bos veya yapilmamis islemler icin log basmayin.

Yeni step dosyalari: her Playwright/network blokundan once/ sonra bir cift log.
"""

from __future__ import annotations

import logging
from abc import ABC

from utils.bot_logging import log_action_done, log_action_start


class BaseStep(ABC):
    def __init__(self) -> None:
        self._log = logging.getLogger(self.__class__.__name__)

    def action_start(self, adim_kodu: str, ne_yapiliyor: str, **extra: object) -> None:
        log_action_start(self._log, adim_kodu, ne_yapiliyor, **extra)

    def action_done(
        self,
        adim_kodu: str,
        sonuc_ozeti: str,
        *,
        basarili: bool,
        **extra: object,
    ) -> None:
        log_action_done(self._log, adim_kodu, sonuc_ozeti, basarili=basarili, **extra)
