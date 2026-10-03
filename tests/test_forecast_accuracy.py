"""Afrekenen van voorspellingen tegen gepubliceerde prijzen."""

from datetime import timedelta

import pytest

from dyntarnl.const import FC_ERROR_CLIP
from dyntarnl.forecast.accuracy import AccuracyTracker
from dyntarnl.forecast.model import QUARTER

from fc_util import DAY_START, NOW, hour_slots

A = "epexpredictor"


def _points(start, values, n=None):
    return [(start + i * QUARTER, v, n) for i, v in enumerate(values)]


def test_hour_settles_as_mean_of_four_quarters():
    tracker = AccuracyTracker()
    target = DAY_START + timedelta(days=1)  # morgen 00:00 lokaal
    fetched = target - timedelta(hours=30)   # looptijd 30 u → bucket d2_3
    tracker.add_snapshot(A, fetched, _points(target, [0.10, 0.12, 0.14, 0.16]))

    settled = tracker.settle(hour_slots(target, [0.11]), NOW)

    assert settled == 4
    stat = tracker.provider_stat(A, "d2_3")
    assert stat.count == 4
    assert stat.bias == pytest.approx(0.13 - 0.11)  # altijd op kale prijzen
    assert stat.mae == pytest.approx(0.02)
    assert tracker.provider_stat(A, "d0_1") is None


def test_each_hour_is_settled_once():
    tracker = AccuracyTracker()
    target = DAY_START + timedelta(days=1)
    tracker.add_snapshot(A, target - timedelta(hours=5), _points(target, [0.1] * 8))
    published = hour_slots(target, [0.1])
    tracker.settle(published, NOW)
    tracker.settle(published, NOW)
    assert tracker.provider_stat(A, "d0_1").count == 4
    # het tweede uur komt later binnen en wordt dan pas afgerekend
    tracker.settle(hour_slots(target, [0.1, 0.2]), NOW)
    stat = tracker.provider_stat(A, "d0_1")
    assert stat.count == 8 and stat.mae == pytest.approx(0.05)


def test_error_is_clipped():
    tracker = AccuracyTracker()
    target = DAY_START + timedelta(days=1)
    tracker.add_snapshot(A, target - timedelta(hours=5), _points(target, [5.0] * 4))
    tracker.settle(hour_slots(target, [0.1]), NOW)
    assert tracker.provider_stat(A, "d0_1").mae == pytest.approx(FC_ERROR_CLIP)


def test_partial_hour_is_not_settled():
    tracker = AccuracyTracker()
    target = DAY_START + timedelta(days=1)
    tracker.add_snapshot(A, target - timedelta(hours=5), _points(target + 2 * QUARTER, [0.1] * 6))
    tracker.settle(hour_slots(target, [0.1, 0.1]), NOW)
    assert tracker.provider_stat(A, "d0_1").count == 4  # alleen het volledige tweede uur


def test_ensemble_settles_per_number_of_sources():
    tracker = AccuracyTracker()
    target = DAY_START + timedelta(days=1)
    tracker.add_snapshot("ensemble", target - timedelta(hours=5), _points(target, [0.12] * 4, n=2))
    tracker.add_snapshot("ensemble", target - timedelta(hours=6), _points(target, [0.15] * 4, n=1))
    tracker.settle(hour_slots(target, [0.10]), NOW)
    assert tracker.ensemble_stat("d0_1", 2).mae == pytest.approx(0.02)
    assert tracker.ensemble_stat("d0_1", 1).mae == pytest.approx(0.05)
    assert tracker.ensemble_stat("d0_1").count == 8
    assert set(tracker.ensemble_by_n()["d0_1"]) == {1, 2}


def test_settled_snapshots_are_pruned_and_window_slides():
    tracker = AccuracyTracker(window_days=28)
    target = DAY_START + timedelta(days=1)
    tracker.add_snapshot(A, target - timedelta(hours=5), _points(target, [0.1] * 4))
    tracker.settle(hour_slots(target, [0.1]), NOW)
    assert tracker.snapshots == []  # volledig afgerekend → ruwe data weg
    assert tracker.provider_stat(A, "d0_1").count == 4

    tracker.prune(NOW + timedelta(days=40))
    assert tracker.provider_stat(A, "d0_1") is None   # buiten het venster
    assert tracker.provider_total(A).count == 4         # totaal blijft


def test_old_unsettled_snapshots_expire():
    tracker = AccuracyTracker()
    tracker.add_snapshot(A, NOW, _points(NOW + timedelta(days=1), [0.1] * 4))
    tracker.prune(NOW + timedelta(days=9))
    assert tracker.snapshots == []


def test_roundtrip_through_storage():
    tracker = AccuracyTracker()
    target = DAY_START + timedelta(days=1)
    tracker.add_snapshot(A, target - timedelta(hours=5), _points(target, [0.12] * 8))
    tracker.settle(hour_slots(target, [0.10]), NOW)

    restored = AccuracyTracker()
    restored.load(tracker.to_dict())
    a, b = restored.provider_stat(A, "d0_1"), tracker.provider_stat(A, "d0_1")
    assert a.count == b.count and a.mae == pytest.approx(b.mae) and a.bias == pytest.approx(b.bias)
    assert len(restored.snapshots) == 1  # tweede uur nog open
