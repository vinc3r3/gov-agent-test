"""Общие утилиты аналитики: результат-«доказательство» (Evidence), статистики, уверенность."""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Evidence:
    """Результат аналитического инструмента. Все числа в ответе агента берутся отсюда."""
    tool: str
    question_type: str                       # search | comparison | analytics | anomaly | fairness | volume
    params: dict[str, Any]                   # период, БИНы, фильтры
    method: str                              # описание метода / формулы
    summary: dict[str, Any]                  # ключевые числа
    sample: dict[str, Any]                   # N объектов, N в выборке сравнения
    confidence: dict[str, Any]               # level + reasons
    limitations: list[str]
    items: list[dict[str, Any]] = field(default_factory=list)   # top-k с ID и ссылками
    columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _clean(asdict(self))


def _clean(x):
    from datetime import date, datetime
    from decimal import Decimal
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, Decimal):
        return round(float(x), 4)
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, (datetime, date)):
        return x.isoformat(sep=" ")[:19] if isinstance(x, datetime) else x.isoformat()
    return x


def median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def quantiles(xs: list[float]) -> tuple[float, float, float] | None:
    if len(xs) < 2:
        return (xs[0], xs[0], xs[0]) if xs else None
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], q[1], q[2]


def mad(xs: list[float]) -> float | None:
    if not xs:
        return None
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def confidence_level(n: int, dispersion: float | None, extra: list[str] | None = None) -> dict:
    """Уверенность по размеру выборки и разбросу (IQR/median)."""
    reasons = list(extra or [])
    if n >= 20 and (dispersion is None or dispersion < 0.6):
        level = "высокая"
    elif n >= 8:
        level = "средняя"
    else:
        level = "низкая"
    reasons.insert(0, f"N сравнения = {n}")
    if dispersion is not None:
        reasons.insert(1, f"разброс цен IQR/медиана = {dispersion:.2f}")
        if dispersion >= 1.0 and level == "высокая":
            level = "средняя"
    return {"level": level, "reasons": reasons}


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100
