"""Nauwkeurigheidsmeting: snapshots bewaren en afrekenen tegen gepubliceerde prijzen.

Keuzes:
- Afrekenen gebeurt ALTIJD op kale prijzen (excl. btw); opslag en energiebelasting
  zijn vast en voegen geen onzekerheid toe.
- De leverancier publiceert per uur. Een uur wordt daarom afgerekend als het
  gemiddelde van de vier voorspelde kwartieren tegen de gepubliceerde uurprijs, en
  telt als vier afgerekende kwartieren. Publiceert een bron ooit per kwartier, dan
  werkt dezelfde code per kwartier.
- Voortschrijdend venster (standaard 28 dagen) i.p.v. exponentiële weging: de MAE
  is dan exact uit te leggen ("over de laatste 28 dagen"), seizoenswisselingen vallen
  er netjes uit, en met dag-aggregaten is het venster exact zonder ruwe data.
- Uitschieters: de fout per kwartier wordt begrensd op ±FC_ERROR_CLIP. Een mediaan
  zou alle ruwe fouten binnen het venster vereisen; dat botst met compacte opslag.
- Ruwe snapshots leven alleen tot ze zijn afgerekend (of FC_SNAPSHOT_MAX_AGE_D);
  daarna blijven alleen dag-aggregaten binnen het venster plus een totaal over.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from ..const import ENSEMBLE, FC_ERROR_CLIP, FC_SNAPSHOT_MAX_AGE_D, LEAD_BUCKETS
from ..model import Slot
from .ensemble import Stat
from .model import QUARTER, lead_bucket

STORAGE_VERSION = 1


def provider_key(provider: str, bucket: str) -> str:
    return f"{provider}|{bucket}"


def ensemble_key(bucket: str, n_sources: int) -> str:
    return f"{ENSEMBLE}|{bucket}|{n_sources}"


def _iso(moment: datetime) -> str:
    return dt_util.as_utc(moment).isoformat()


def _parse(value: str) -> datetime:
    return dt_util.as_utc(dt_util.parse_datetime(value))


class AccuracyTracker:
    """Snapshots + aggregaten. Serialiseerbaar naar/van een HA Store."""

    def __init__(self, window_days: int = 28) -> None:
        self.window_days = window_days
        # snapshot: {"p": provider, "f": fetched_at, "s": eerste kwartier,
        #            "v": [prijs|None per kwartier], "n": [n_sources] (alleen ensemble),
        #            "u": afgerekend t/m (UTC)}
        self.snapshots: list[dict] = []
        # dag (UTC-datum van het doeluur) -> sleutel -> [kwartieren, Σ|fout|, Σfout]
        self.daily: dict[str, dict[str, list[float]]] = {}
        # Totaal sinds het begin (blijft, ook als het venster verschuift).
        self.total: dict[str, list[float]] = {}

    # --- snapshots ---------------------------------------------------------

    def add_snapshot(
        self,
        provider: str,
        fetched_at: datetime,
        points: Iterable[tuple[datetime, float, int | None]],
    ) -> None:
        """Bewaar een voorspelling als compacte array vanaf het eerste kwartier."""
        points = sorted(points, key=lambda p: p[0])
        if not points:
            return
        first = points[0][0]
        length = int((points[-1][0] - first) / QUARTER) + 1
        values: list[float | None] = [None] * length
        ns: list[int | None] = [None] * length
        for start, value, n in points:
            i = int((start - first) / QUARTER)
            values[i] = round(value, 6)
            ns[i] = n
        snap = {"p": provider, "f": _iso(fetched_at), "s": _iso(first), "v": values, "u": _iso(first)}
        if provider == ENSEMBLE:
            snap["n"] = ns
        self.snapshots.append(snap)

    # --- afrekenen ---------------------------------------------------------

    def settle(self, published: Iterable[Slot], now: datetime) -> int:
        """Reken snapshots af tegen gepubliceerde slots (kale prijs, market_ex).

        Elk (snapshot, slot)-paar wordt hooguit één keer afgerekend: per snapshot
        houden we bij tot waar hij is verwerkt. Geeft het aantal afgerekende
        kwartieren terug.
        """
        slots = sorted(
            ((dt_util.as_utc(s.start), dt_util.as_utc(s.end), s.market_ex) for s in published),
            key=lambda s: s[0],
        )
        if not slots:
            self.prune(now)
            return 0
        published_end = slots[-1][1]
        settled = 0

        for snap in self.snapshots:
            first = _parse(snap["s"])
            fetched = _parse(snap["f"])
            values = snap["v"]
            snap_end = first + len(values) * QUARTER
            until = _parse(snap["u"])
            for start, end, actual in slots:
                if start < until or end > snap_end:
                    continue
                i0 = int((start - first) / QUARTER)
                i1 = int((end - first) / QUARTER)
                if i0 < 0:
                    continue
                window = values[i0:i1]
                if not window or any(v is None for v in window):
                    continue
                bucket = lead_bucket(start, fetched)
                if bucket is None:
                    continue
                error = sum(window) / len(window) - actual
                error = max(-FC_ERROR_CLIP, min(FC_ERROR_CLIP, error))
                if snap["p"] == ENSEMBLE:
                    n = min(x for x in snap["n"][i0:i1] if x is not None)
                    key = ensemble_key(bucket, n)
                else:
                    key = provider_key(snap["p"], bucket)
                self._add(start.date().isoformat(), key, len(window), error)
                settled += len(window)
            new_until = min(published_end, snap_end)
            if new_until > until:
                snap["u"] = _iso(new_until)

        self.prune(now)
        return settled

    def _add(self, day: str, key: str, quarters: int, error: float) -> None:
        for bucket in (self.daily.setdefault(day, {}), self.total):
            agg = bucket.setdefault(key, [0, 0.0, 0.0])
            agg[0] += quarters
            agg[1] += abs(error) * quarters
            agg[2] += error * quarters

    def prune(self, now: datetime) -> None:
        """Afgerekende of te oude snapshots en dag-aggregaten buiten het venster weg."""
        max_age = now - timedelta(days=FC_SNAPSHOT_MAX_AGE_D)
        keep = []
        for snap in self.snapshots:
            end = _parse(snap["s"]) + len(snap["v"]) * QUARTER
            if _parse(snap["u"]) >= end or _parse(snap["f"]) < max_age:
                continue
            keep.append(snap)
        self.snapshots = keep
        oldest = (dt_util.as_utc(now) - timedelta(days=self.window_days)).date().isoformat()
        self.daily = {d: v for d, v in self.daily.items() if d > oldest}

    # --- statistiek --------------------------------------------------------

    def _sum(self, keys: Iterable[str], source: dict[str, list[float]] | None = None) -> Stat | None:
        count = abs_sum = signed = 0.0
        keys = set(keys)
        sources = [source] if source is not None else list(self.daily.values())
        for day in sources:
            for key, (c, a, s) in day.items():
                if key in keys:
                    count += c
                    abs_sum += a
                    signed += s
        if not count:
            return None
        return Stat(count=int(count), mae=abs_sum / count, bias=signed / count)

    def provider_stat(self, provider: str, bucket: str | None) -> Stat | None:
        if bucket is None:
            return None
        return self._sum([provider_key(provider, bucket)])

    def provider_total(self, provider: str) -> Stat | None:
        return self._sum(
            [provider_key(provider, b) for b, _, _ in LEAD_BUCKETS], source=self.total
        )

    def provider_window(self, provider: str) -> Stat | None:
        return self._sum([provider_key(provider, b) for b, _, _ in LEAD_BUCKETS])

    def ensemble_stat(self, bucket: str | None, n_sources: int | None = None) -> Stat | None:
        """Gemeten ensemble-fout per bucket, en (optioneel) per aantal bronnen."""
        if bucket is None:
            return None
        keys = {
            k
            for day in self.daily.values()
            for k in day
            if k.startswith(f"{ENSEMBLE}|{bucket}|")
            and (n_sources is None or k == ensemble_key(bucket, n_sources))
        }
        return self._sum(keys)

    def ensemble_by_n(self) -> dict[str, dict[int, Stat]]:
        """Voor diagnostiek: per bucket per aantal bronnen."""
        out: dict[str, dict[int, Stat]] = {}
        for bucket, _, _ in LEAD_BUCKETS:
            ns = {
                int(k.rsplit("|", 1)[1])
                for day in self.daily.values()
                for k in day
                if k.startswith(f"{ENSEMBLE}|{bucket}|")
            }
            for n in sorted(ns):
                stat = self.ensemble_stat(bucket, n)
                if stat:
                    out.setdefault(bucket, {})[n] = stat
        return out

    # --- opslag ------------------------------------------------------------

    def to_dict(self) -> dict:
        def rounded(d: dict[str, list[float]]) -> dict[str, list[float]]:
            return {k: [int(v[0]), round(v[1], 6), round(v[2], 6)] for k, v in d.items()}

        return {
            "snapshots": self.snapshots,
            "daily": {day: rounded(v) for day, v in self.daily.items()},
            "total": rounded(self.total),
        }

    def load(self, data: dict | None) -> None:
        if not data:
            return
        self.snapshots = list(data.get("snapshots") or [])
        self.daily = {d: dict(v) for d, v in (data.get("daily") or {}).items()}
        self.total = dict(data.get("total") or {})
