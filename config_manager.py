import os
from pathlib import Path
from typing import Dict, Optional


class ConfigManager:
    def __init__(self, env_path: str = ".env"):
        self.env_path = Path(env_path)
        self._file_values: Dict[str, str] = {}
        self._load_env_file()

    def _load_env_file(self):
        if not self.env_path.exists():
            return

        for raw_line in self.env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            self._file_values[key.strip()] = value.strip().strip("\"'")

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return os.getenv(key) or self._file_values.get(key, default)

    def get_int(self, key: str, default: int) -> int:
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def get_float(self, key: str, default: float) -> float:
        value = self.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def get_required(self, key: str) -> str:
        value = self.get(key)
        if value is None or value == "":
            raise ValueError(f"Eksik zorunlu ortam degiskeni: {key}")
        return value
