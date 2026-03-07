"""Memory salience classification and archival rules."""

from __future__ import annotations

import time

from pawbot.agent.memory._compat import MEMORY_TYPE_CONFIG, coerce_float as _coerce_float


class MemoryClassifier:
    @staticmethod
    def calculate_salience(base_salience: float, type: str, created_at: int, last_accessed: int) -> float:
        cfg = MEMORY_TYPE_CONFIG.get(type, {"half_life_days": 180})
        half_life = _coerce_float(cfg.get("half_life_days", 180), 180.0)
        now = int(time.time())
        age_days = max(0.0, (now - created_at) / 86400)
        decayed = base_salience * (0.5 ** (age_days / half_life))
        days_since_access = max(0.0, (now - last_accessed) / 86400)
        if days_since_access <= 7:
            decayed = min(1.0, decayed + 0.2)
        return round(max(0.0, decayed), 4)

    @staticmethod
    def should_archive(salience: float, type: str) -> bool:
        if type == "reflection":
            return False
        min_sal = _coerce_float(MEMORY_TYPE_CONFIG.get(type, {}).get("min_salience", 0.1), 0.1)
        return salience < min_sal
