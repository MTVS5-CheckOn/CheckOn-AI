"""threshold 기본값 ↔ 04_threshold_config.md §1·§3.1 값 대조 — 몰래 변경 방지.

이 상수는 04 문서 §1 표·§3.1 표를 그대로 옮긴 것이다. 문서가 원본이고 이 파일은
대조표다 — 코드 기본값을 여기 상수에 맞춰 고치지 말 것. 어긋나면 골든셋 기대값이 깨진다.
"""

from ai.detection.thresholds import default_threshold_config


def test_r1_matches_doc() -> None:
    r1 = default_threshold_config().r1
    # 04 §1 R1 재정의(2026-08-03) — 발동률 목표 방식. drop_pp는 **폴백** 지위다.
    assert r1.target_alert_rate == 0.05
    assert r1.quantile_min_pool == 100
    assert r1.drop_pp == 15.0
    assert r1.consecutive_weeks == 2
    assert r1.saturation_drop_pp == 25.0  # §3.1 보정 상한


def test_r2_matches_doc() -> None:
    r2 = default_threshold_config().r2
    assert r2.submit_drop_pp == 25.0
    assert r2.consecutive_missing == 3
    assert r2.saturation_drop_pp == 40.0
    assert r2.saturation_missing == 5


def test_r3_matches_doc() -> None:
    r3 = default_threshold_config().r3
    assert r3.volume_ratio == 0.4
    assert r3.min_baseline_events == 10


def test_r4_matches_doc() -> None:
    r4 = default_threshold_config().r4
    assert r4.acc_stable_band_pp == 5.0
    assert r4.time_ratio == 1.5
    assert r4.consecutive_weeks == 2
    assert r4.saturation_time_ratio == 2.0


def test_r5_matches_doc() -> None:
    assert default_threshold_config().r5.care_window_weeks == 2


def test_r6_matches_doc() -> None:
    r6 = default_threshold_config().r6
    assert r6.cell_error_share == 0.5
    assert r6.cell_min_items == 10
    assert r6.cell_acc_below == 0.5
    assert r6.tagging_rate_min == 0.6
    assert r6.saturation_error_share == 0.6


def test_caps_and_baseline_match_doc() -> None:
    config = default_threshold_config()
    assert config.cap_min == 3  # 04 §3 TOP 3~5
    assert config.cap_max == 5
    assert config.baseline_window_weeks == 8  # 04 §1 이동 8주


def test_segment_coefficients_match_doc() -> None:
    seg = default_threshold_config().segments
    assert seg.readapt_relax == 1.3  # 04 §2
    assert seg.new_term_relax == 1.2
    assert seg.readapt_window_weeks == 4


def test_default_source_and_version() -> None:
    config = default_threshold_config()
    # v2 — R1 발동률 목표 전환(04 §1 재정의 · 거동 변경이라 버전 인상).
    assert config.version == 2
    assert config.source == "default"
    assert config.threshold_version == "default-v2"
