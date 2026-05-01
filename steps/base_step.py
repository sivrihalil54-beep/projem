"""Tum adim siniflari icin ortak temel."""

from __future__ import annotations

import logging
from abc import ABC


class BaseStep(ABC):
    def __init__(self) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
