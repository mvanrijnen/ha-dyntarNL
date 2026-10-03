"""Ensemble: gewogen gemiddelde over providers, met gewichten uit gemeten MAE.

Alles hier is puur (geen HA, geen netwerk), zodat het los te testen is.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..const import WEIGHTING_ACCURACY
from .model import ForecastConfig, ProviderSeries, lead_bucket

_MIN_MAE = 1e-6  # nooit delen door nul


@dataclass(frozen=True, slots=True)
class Stat:
    """Gemeten nauwkeurigheid over het venster (alles op kale prijzen, €/kWh)."""

    count: int     # afgerekende kwartieren
    mae: float     # gemiddelde absolute fout (begrensd)
    bias: float    # gemiddelde signed fout: voorspeld − werkelijk


type StatFn = Callable[[str, str | None], Stat | None]


def normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {p: 1 / len(weights) for p in weights}
    return {p: w / total for p, w in weights.items()}


def apply_floor(weights: dict[str, float], floor: float) -> dict[str, float]:
    """Ondergrens per gewicht; de rest wordt naar verhouding herverdeeld.

    Een provider die een slechte periode had valt zo nooit helemaal weg, en kan
    zich dus terugverdienen zodra hij weer goed voorspelt.
    """
    if not weights:
        return {}
    floor = min(max(floor, 0.0), 1 / len(weights))
    fixed: dict[str, float] = {}
    result = dict(weights)
    while True:
        low = [p for p, w in result.items() if p not in fixed and w < floor]
        if not low:
            return result
        for p in low:
            fixed[p] = floor
        free = {p: weights[p] for p in weights if p not in fixed}
        remaining = 1 - floor * len(fixed)
        free_total = sum(free.values())
        result = {**fixed}
        for p, w in free.items():
            result[p] = (w / free_total * remaining) if free_total > 0 else remaining / len(free)


def compute_weights(stats: dict[str, Stat | None], cfg: ForecastConfig) -> dict[str, float]:
    """Gewicht per provider: ~ 1/MAE^k, genormaliseerd, met ondergrens.

    Gelijke gewichten bij weging 'gelijk', of zolang een van de providers nog te
    weinig afgerekende kwartieren heeft (`min_samples`).
    """
    if not stats:
        return {}
    equal = {p: 1 / len(stats) for p in stats}
    if cfg.weighting != WEIGHTING_ACCURACY or len(stats) == 1:
        return equal
    if any(s is None or s.count < cfg.min_samples for s in stats.values()):
        return equal
    raw = {p: 1 / max(s.mae, _MIN_MAE) ** cfg.weight_exponent for p, s in stats.items()}
    return apply_floor(normalize(raw), cfg.weight_floor)


@dataclass(slots=True)
class Combined:
    start: datetime
    value: float
    providers: tuple[str, ...]
    weights: dict[str, float]
    spread_min: float
    spread_max: float
    fetched_at: datetime
    expanded: bool

    @property
    def n_sources(self) -> int:
        return len(self.providers)

    @property
    def spread(self) -> float:
        return self.spread_max - self.spread_min


def corrected(value: float, stat: Stat | None, cfg: ForecastConfig) -> float:
    """Biascorrectie (optioneel): de gemeten systematische fout eraf halen."""
    if cfg.bias_correction and stat is not None and stat.count >= cfg.min_samples:
        return value - stat.bias
    return value


def combine(
    series: list[ProviderSeries], stat_fn: StatFn, cfg: ForecastConfig
) -> dict[datetime, Combined]:
    """Gewogen gemiddelde per kwartier over de providers die er een waarde voor hebben.

    De bucket (en dus het gewicht) hangt per provider af van zíjn looptijd:
    doelkwartier − zijn ophaalmoment. Heeft maar één provider een waarde, dan is
    dat de uitkomst (n_sources = 1).
    """
    per_start: dict[datetime, list[tuple[ProviderSeries, float, bool]]] = {}
    for s in series:
        for p in s.points:
            per_start.setdefault(p.start, []).append((s, p.price_raw, p.expanded))

    out: dict[datetime, Combined] = {}
    for start in sorted(per_start):
        entries = per_start[start]
        stats = {s.provider: stat_fn(s.provider, lead_bucket(start, s.fetched_at)) for s, _, _ in entries}
        weights = compute_weights(stats, cfg)
        values = {s.provider: corrected(v, stats[s.provider], cfg) for s, v, _ in entries}
        out[start] = Combined(
            start=start,
            value=sum(weights[p] * v for p, v in values.items()),
            providers=tuple(values),
            weights=weights,
            spread_min=min(values.values()),
            spread_max=max(values.values()),
            fetched_at=max(s.fetched_at for s, _, _ in entries),
            expanded=any(e for _, _, e in entries),
        )
    return out


def expected_error(mae: float | None, spread: float | None) -> float | None:
    """Verwachte fout = gemeten MAE gecombineerd met de actuele onenigheid.

    √(MAE² + (spreiding/2)²): de gemeten fout van het ensemble voor deze looptijd en
    dit aantal bronnen, verhoogd wanneer de providers het nu extra oneens zijn.
    Zonder meting blijft alleen de halve spreiding over (of None bij één bron).
    """
    half = spread / 2 if spread else 0.0
    if mae is None:
        return half or None
    return math.sqrt(mae**2 + half**2)
