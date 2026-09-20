"""API-level tests via FastAPI TestClient."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SUPPORTED = ["en-US", "zh-CN", "zh-TW", "ja-JP"]
DEFAULT = "en-US"


def _post(payload):
    return client.post("/api/v1/negotiate", json=payload)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "language-negotiator",
    }


def test_negotiate_success():
    response = _post(
        {
            "accept_language": "zh-CN,zh;q=0.9,en;q=0.8,*;q=0.5",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_language"] == "zh-CN"
    assert body["selected_supported_index"] == 1
    assert body["fallback"] is False
    assert len(body["candidates"]) == 4
    zh_cn = body["candidates"][1]
    assert zh_cn["status"] == "selected"
    # Truncation trace: full tag matches directly.
    assert zh_cn["events"][0] == {
        "truncated_to": "zh-cn",
        "outcome": "match",
        "matched_range": "zh-cn",
        "q": 1.0,
        "header_position": 1,
        "detail": None,
    }


def test_missing_header_default_fallback():
    response = _post(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_language"] == "en-US"
    assert body["fallback"] is True
    assert body["fallback_reason"] == "no-valid-range"
    assert body["parsed_ranges"] == []


def test_invalid_accept_language_returns_422():
    response = _post(
        {
            "accept_language": "en;q=2",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "accept_language"]
    assert detail[0]["type"] == "accept_language.syntax_error"


def test_default_not_in_supported_returns_422():
    response = _post(
        {
            "accept_language": "en",
            "supported_languages": SUPPORTED,
            "default_language": "fr-FR",
        }
    )
    assert response.status_code == 422


def test_duplicate_supported_tags_returns_422():
    response = _post(
        {
            "supported_languages": ["en", "EN"],
            "default_language": "en",
        }
    )
    assert response.status_code == 422


def test_empty_supported_returns_422():
    response = _post(
        {"supported_languages": [], "default_language": "en"}
    )
    assert response.status_code == 422


def test_extra_field_rejected_422():
    response = _post(
        {
            "supported_languages": ["en"],
            "default_language": "en",
            "unexpected": 1,
        }
    )
    assert response.status_code == 422


def test_q_zero_trace_and_fallback():
    response = _post(
        {
            "accept_language": "*;q=0",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_language"] == "en-US"
    assert body["fallback"] is True
    assert all(c["status"] == "excluded" for c in body["candidates"])
    assert "q=0" in body["message"]


def test_wildcard_and_duplicates():
    response = _post(
        {
            "accept_language": "de;q=0.9,en;q=0.1,en;q=0.5,*;q=0.05",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    body = response.json()
    # en wins among wildcard candidates; duplicate second en ignored.
    assert body["selected_language"] == "en-US"
    assert body["ignored_duplicates"] == [
        {"range": "en", "position": 3}
    ]


def test_locale_fallback_chain_success_omission_rule():
    response = _post(
        {
            "accept_language": "gsw;q=0.9,fr;q=0.5",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": {
                "gsw": "de",
                "de": "zh-TW",
                "fr": "ja-JP",
            },
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_language"] == "zh-TW"
    assert body["fallback"] is False
    assert body["locale_fallback"] == {
        "triggered": True,
        "source_range": "gsw",
        "source_q": 0.9,
        "source_position": 1,
        "path": ["gsw", "de", "zh-tw"],
        "selected_language": "zh-TW",
    }


def test_locale_fallback_key_absent_in_legacy_responses():
    # Field not provided -> the response shape must stay exactly legacy.
    response = _post(
        {
            "accept_language": "de,*;q=0.05",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    assert "locale_fallback" not in response.json()

    # Field provided but chain not triggered -> still omitted.
    response = _post(
        {
            "accept_language": "de;q=0",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": {"de": "zh-CN"},
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback"] is True
    assert "locale_fallback" not in body


def test_locale_fallback_q_zero_concrete_blocks_selection():
    # zh-TW is reached but excluded by a concrete q=0 prefix range.
    response = _post(
        {
            "accept_language": "de,zh;q=0",
            "supported_languages": ["zh-TW", "ja-JP"],
            "default_language": "ja-JP",
            "locale_fallbacks": {"de": "zh-TW", "zh-TW": "ja-JP"},
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_language"] == "ja-JP"
    assert body["locale_fallback"]["path"] == ["de", "zh-tw", "ja-jp"]


def test_locale_fallback_duplicate_sources_422():
    response = _post(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": {"DE": "zh-CN", "de": "zh-TW"},
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "locale_fallbacks"]
    assert "重复" in detail[0]["msg"]


def test_locale_fallback_illegal_tag_422():
    response = _post(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": {"de": "not a tag"},
        }
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "locale_fallbacks"]


def test_locale_fallback_cycle_422():
    response = _post(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": {"de": "fr", "fr": "de"},
        }
    )
    assert response.status_code == 422
    assert "环路" in response.json()["detail"][0]["msg"]


def test_locale_fallback_dangling_chain_422():
    response = _post(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": {"de": "xx", "xx": "yy"},
        }
    )
    assert response.status_code == 422
    assert "悬空" in response.json()["detail"][0]["msg"]


def test_locale_fallback_must_be_object_422():
    response = _post(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": [["de", "fr"]],
        }
    )
    assert response.status_code == 422


def _post_q_stability(payload):
    return client.post("/api/v1/negotiate/q-stability", json=payload)


def test_q_stability_success():
    response = _post_q_stability(
        {
            "accept_language": "zh-CN,zh;q=0.9,en;q=0.8,*;q=0.5",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    # The baseline is the existing single-person negotiation result.
    assert body["baseline"]["selected_language"] == "zh-CN"
    assert body["baseline"]["fallback"] is False
    assert "locale_fallback" not in body["baseline"]
    ranges = body["ranges"]
    assert [r["range"] for r in ranges] == ["zh-cn", "zh", "en", "*"]
    assert [r["position"] for r in ranges] == [1, 2, 3, 4]
    assert [r["original_q"] for r in ranges] == [1.0, 0.9, 0.8, 0.5]
    first = ranges[0]
    assert first["lower_bound"] == 0.9
    assert first["upper_bound"] == 1.0
    assert first["upper_outside"] is None
    assert first["lower_outside"] == {
        "q": 0.899,
        "selected_language": "zh-TW",
        "fallback": False,
    }


def test_q_stability_interval_contains_original_q():
    response = _post_q_stability(
        {
            "accept_language": "en;q=0.333",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    report = response.json()["ranges"][0]
    assert report["lower_bound"] <= report["original_q"] <= report["upper_bound"]
    # Only one positive-q concrete range: the result cannot change by
    # moving its q anywhere in 0.001..1.000.
    assert report["lower_bound"] == 0.001
    assert report["upper_bound"] == 1.0
    assert report["upper_outside"] is None
    assert report["lower_outside"]["fallback"] is True


def test_q_stability_deduplicated_ranges_keep_first_position():
    response = _post_q_stability(
        {
            "accept_language": "de;q=0.9,en;q=0.1,en;q=0.5,*;q=0.05",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    body = response.json()
    # Duplicate second en is ignored by negotiate and absent from reports.
    assert [r["range"] for r in body["ranges"]] == ["de", "en", "*"]
    en_report = body["ranges"][1]
    assert en_report["position"] == 2
    assert en_report["original_q"] == 0.1


def test_q_stability_blank_header_empty_ranges():
    response = _post_q_stability(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ranges"] == []
    assert body["baseline"]["fallback"] is True
    assert body["baseline"]["fallback_reason"] == "no-valid-range"


def test_q_stability_wildcard_outside_point_shows_fallback():
    response = _post_q_stability(
        {
            "accept_language": "*;q=0.5",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    report = response.json()["ranges"][0]
    assert report["range"] == "*"
    assert report["lower_bound"] == 0.001
    assert report["lower_outside"] == {
        "q": 0.0,
        "selected_language": "en-US",
        "fallback": True,
    }


def test_q_stability_locale_fallback_baseline_key_present():
    response = _post_q_stability(
        {
            "accept_language": "gsw;q=0.9,fr;q=0.5",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "locale_fallbacks": {
                "gsw": "de",
                "de": "zh-TW",
                "fr": "ja-JP",
            },
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["baseline"]["locale_fallback"]["selected_language"] == "zh-TW"
    gsw = body["ranges"][0]
    # The chain follows the highest-q positive source: while gsw stays
    # at or above fr's 0.5 it reaches zh-TW (header order wins the tie);
    # below that fr takes over and its chain ends at ja-JP.
    assert gsw["lower_bound"] == 0.5
    assert gsw["upper_bound"] == 1.0
    assert gsw["lower_outside"] == {
        "q": 0.499,
        "selected_language": "ja-JP",
        "fallback": False,
    }


def test_q_stability_invalid_header_returns_422():
    response = _post_q_stability(
        {
            "accept_language": "en;q=2",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "accept_language"]
    assert detail[0]["type"] == "accept_language.syntax_error"


def test_q_stability_default_not_in_supported_returns_422():
    response = _post_q_stability(
        {
            "accept_language": "en",
            "supported_languages": SUPPORTED,
            "default_language": "fr-FR",
        }
    )
    assert response.status_code == 422


def test_q_stability_extra_field_rejected_422():
    response = _post_q_stability(
        {
            "supported_languages": ["en"],
            "default_language": "en",
            "unexpected": 1,
        }
    )
    assert response.status_code == 422


def _post_q_steer(payload):
    return client.post("/api/v1/negotiate/q-steer", json=payload)


def test_q_steer_recommended():
    response = _post_q_steer(
        {
            "accept_language": "zh-CN,zh;q=0.9,en;q=0.8,*;q=0.5",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "zh-TW",
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recommended"
    assert body["target_language"] == "zh-TW"
    assert body["baseline"]["selected_language"] == "zh-CN"
    assert "locale_fallback" not in body["baseline"]
    # Lowering zh-CN's own range below zh;q=0.9 lets zh-TW win.
    assert body["recommendation"] == {
        "range": "zh-cn",
        "position": 1,
        "original_q": 1.0,
        "new_q": 0.899,
    }
    assert body["adjusted"]["selected_language"] == "zh-TW"
    assert body["adjusted"]["fallback"] is False
    assert body["ranges"] is None


def test_q_steer_already_selected():
    response = _post_q_steer(
        {
            "accept_language": "zh-CN,zh;q=0.9",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            # Case-insensitive: the canonical server casing is echoed.
            "target_language": "zh-cn",
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "already-selected"
    assert body["target_language"] == "zh-CN"
    assert body["adjusted"] == body["baseline"]
    assert body["recommendation"] is None
    assert body["ranges"] is None


def test_q_steer_unreachable():
    response = _post_q_steer(
        {
            "accept_language": "en,zh",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "ja-JP",
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unreachable"
    assert body["adjusted"] is None
    assert body["recommendation"] is None
    assert body["ranges"] == [
        {"range": "en", "position": 1, "original_q": 1.0, "reachable": False},
        {"range": "zh", "position": 2, "original_q": 1.0, "reachable": False},
    ]


def test_q_steer_blank_header_target_not_default_unreachable():
    response = _post_q_steer(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "zh-CN",
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unreachable"
    assert body["ranges"] == []
    assert body["baseline"]["fallback"] is True


def test_q_steer_blank_header_target_is_default_already_selected():
    response = _post_q_steer(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "en-US",
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "already-selected"
    assert body["baseline"]["fallback"] is True


def test_q_steer_locale_fallback_adjusted_report():
    response = _post_q_steer(
        {
            "accept_language": "gsw;q=0.9,fr;q=0.5",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "ja-JP",
            "locale_fallbacks": {"gsw": "zh-TW", "fr": "ja-JP"},
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recommended"
    assert body["recommendation"]["range"] == "gsw"
    assert body["recommendation"]["new_q"] == 0.499
    assert body["baseline"]["locale_fallback"]["source_range"] == "gsw"
    assert body["adjusted"]["selected_language"] == "ja-JP"
    assert body["adjusted"]["locale_fallback"]["source_range"] == "fr"


def test_q_steer_target_not_in_supported_422():
    response = _post_q_steer(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "fr-FR",
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "target_language"]
    assert "supported_languages" in detail[0]["msg"]


def test_q_steer_target_invalid_tag_422():
    response = _post_q_steer(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "not a tag",
        }
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "target_language"]


def test_q_steer_target_missing_422():
    response = _post_q_steer(
        {
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422


def test_q_steer_invalid_header_returns_422():
    response = _post_q_steer(
        {
            "accept_language": "en;q=2",
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
            "target_language": "zh-CN",
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "accept_language"]
    assert detail[0]["type"] == "accept_language.syntax_error"


def test_q_steer_extra_field_rejected_422():
    response = _post_q_steer(
        {
            "supported_languages": ["en"],
            "default_language": "en",
            "target_language": "en",
            "unexpected": 1,
        }
    )
    assert response.status_code == 422


def _post_group(payload):
    return client.post("/api/v1/negotiate/group", json=payload)


def test_group_negotiate_success():
    response = _post_group(
        {
            "participants": [
                {"id": "alice", "accept_language": "zh;q=0.9,en;q=0.8"},
                {"id": "bob", "accept_language": "zh;q=0.7"},
                {"id": "carol"},
            ],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_language"] == "zh-CN"
    assert body["selected_supported_index"] == 1
    assert body["fallback"] is False
    assert body["fallback_reason"] is None
    # carol provided no header -> abstains (accepts anything, never vetoes).
    assert body["participants"][2]["abstained"] is True
    zh_cn = body["candidates"][1]
    assert zh_cn["status"] == "selected"
    assert zh_cn["min_q"] == 0.7
    assert zh_cn["total_q"] == 2.6
    assert zh_cn["vetoed"] is False
    assert zh_cn["vetoed_by"] == []
    assert [w["matched_range"] for w in zh_cn["weights"]] == [
        "zh",
        "zh",
        None,
    ]


def test_group_concrete_q_zero_veto():
    response = _post_group(
        {
            "participants": [
                {"id": "alice", "accept_language": "zh;q=0,*;q=0.6"},
                {"id": "bob", "accept_language": "zh;q=0.9,*;q=0.7"},
            ],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    # zh-CN/zh-TW are vetoed by alice despite bob's q=0.9.
    assert body["selected_language"] == "en-US"
    zh_cn = body["candidates"][1]
    assert zh_cn["vetoed"] is True
    assert zh_cn["vetoed_by"] == ["alice"]
    assert zh_cn["status"] == "vetoed"


def test_group_fallback_when_no_common_candidate():
    response = _post_group(
        {
            "participants": [
                {"id": "alice", "accept_language": "en;q=0"},
                {"id": "bob", "accept_language": "zh;q=0,ja;q=0"},
            ],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_language"] == "en-US"
    assert body["fallback"] is True
    assert body["fallback_reason"] == "no-common-acceptable-candidate"


def test_group_empty_participants_422():
    response = _post_group(
        {
            "participants": [],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422


def test_group_duplicate_participant_id_422():
    response = _post_group(
        {
            "participants": [
                {"id": "alice", "accept_language": "en"},
                {"id": "alice", "accept_language": "zh"},
            ],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "participants"]
    assert "重复" in detail[0]["msg"]


def test_group_participant_invalid_header_422():
    response = _post_group(
        {
            "participants": [
                {"id": "alice", "accept_language": "en"},
                {"id": "bob", "accept_language": "fr;q=2"},
            ],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "participants", 1, "accept_language"]
    assert detail[0]["type"] == "accept_language.syntax_error"
    assert "bob" in detail[0]["msg"]


def test_group_default_not_in_supported_422():
    response = _post_group(
        {
            "participants": [{"id": "alice"}],
            "supported_languages": SUPPORTED,
            "default_language": "fr-FR",
        }
    )
    assert response.status_code == 422


def test_group_missing_participant_id_422():
    response = _post_group(
        {
            "participants": [{"accept_language": "en"}],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422


def test_group_extra_field_rejected_422():
    response = _post_group(
        {
            "participants": [{"id": "alice", "role": "admin"}],
            "supported_languages": SUPPORTED,
            "default_language": DEFAULT,
        }
    )
    assert response.status_code == 422


def _post_coverage(payload):
    return client.post("/api/v1/negotiate/coverage-plan", json=payload)


def test_coverage_plan_basic():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [
                {"accept_language": "fr;q=0.9", "traffic": 100},
                {"accept_language": "de", "traffic": 50},
                {"accept_language": "en", "traffic": 200},
            ],
            "candidates": ["fr-FR", "de-DE"],
            "max_additions": 2,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_additions"] == ["fr-FR", "de-DE"]
    assert body["baseline_fallback_traffic"] == 150.0
    assert body["improved_traffic"] == 150.0
    samples = body["samples"]
    assert [s["position"] for s in samples] == [1, 2, 3]
    assert [s["traffic"] for s in samples] == [100.0, 50.0, 200.0]
    assert samples[0]["baseline_selected_language"] == "en-US"
    assert samples[0]["baseline_fallback"] is True
    assert samples[0]["planned_selected_language"] == "fr-FR"
    assert samples[0]["planned_fallback"] is False
    assert samples[0]["improved"] is True
    assert samples[1]["planned_selected_language"] == "de-DE"
    assert samples[1]["improved"] is True
    # en already hits en-US in the baseline: unchanged, not improved.
    assert samples[2]["baseline_fallback"] is False
    assert samples[2]["planned_selected_language"] == "en-US"
    assert samples[2]["improved"] is False


def test_coverage_plan_tie_prefers_fewer_additions():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [{"accept_language": "fr", "traffic": 100}],
            "candidates": ["fr-FR", "es-ES"],
            "max_additions": 2,
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_additions"] == ["fr-FR"]
    assert body["improved_traffic"] == 100.0


def test_coverage_plan_tie_prefers_earlier_candidate_input_order():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [{"accept_language": "de,fr", "traffic": 80}],
            "candidates": ["de-DE", "fr-FR"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 200
    assert response.json()["selected_additions"] == ["de-DE"]


def test_coverage_plan_no_improvement_selects_empty():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [
                {"accept_language": "fr;q=0", "traffic": 60},
                {"traffic": 30},
            ],
            "candidates": ["fr-FR", "de-DE"],
            "max_additions": 2,
        }
    )
    assert response.status_code == 200
    body = response.json()
    # fr stays q=0-excluded even after being added; the headerless sample
    # always falls back. Nothing improves -> recommend adding nothing.
    assert body["selected_additions"] == []
    assert body["improved_traffic"] == 0.0
    assert body["baseline_fallback_traffic"] == 90.0
    assert [s["improved"] for s in body["samples"]] == [False, False]
    assert all(s["planned_fallback"] for s in body["samples"])


def test_coverage_plan_q_zero_exclusion_blocks_conversion():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [
                {"accept_language": "en;q=0,fr;q=0.9", "traffic": 120},
            ],
            "candidates": ["fr-FR"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 200
    body = response.json()
    # en-US is q=0-excluded in the baseline; adding fr-FR converts the
    # sample to a normal hit.
    assert body["baseline_fallback_traffic"] == 120.0
    assert body["selected_additions"] == ["fr-FR"]
    assert body["improved_traffic"] == 120.0
    sample = body["samples"][0]
    assert sample["baseline_fallback"] is True
    assert sample["planned_selected_language"] == "fr-FR"
    assert sample["improved"] is True


def test_coverage_plan_negative_traffic_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [{"accept_language": "fr", "traffic": -1}],
            "candidates": ["fr-FR"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 422


def test_coverage_plan_empty_samples_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [],
            "candidates": ["fr-FR"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 422


def test_coverage_plan_candidates_bounds_422():
    base = {
        "supported_languages": ["en-US"],
        "default_language": "en-US",
        "samples": [{"accept_language": "fr", "traffic": 1}],
        "max_additions": 1,
    }
    assert _post_coverage({**base, "candidates": []}).status_code == 422
    thirteen = [f"q{i}z" for i in range(13)]
    assert (
        _post_coverage({**base, "candidates": thirteen}).status_code == 422
    )


def test_coverage_plan_max_additions_bounds_422():
    base = {
        "supported_languages": ["en-US"],
        "default_language": "en-US",
        "samples": [{"accept_language": "fr", "traffic": 1}],
        "candidates": ["fr-FR"],
    }
    assert _post_coverage({**base, "max_additions": 0}).status_code == 422
    assert _post_coverage({**base, "max_additions": 4}).status_code == 422


def test_coverage_plan_duplicate_candidates_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [{"accept_language": "fr", "traffic": 1}],
            "candidates": ["fr-FR", "FR-fr"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 422
    assert "重复" in response.json()["detail"][0]["msg"]


def test_coverage_plan_candidate_already_supported_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US", "fr-FR"],
            "default_language": "en-US",
            "samples": [{"accept_language": "fr", "traffic": 1}],
            "candidates": ["FR-fr"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 422
    assert "重复" in response.json()["detail"][0]["msg"]


def test_coverage_plan_invalid_candidate_tag_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [{"accept_language": "fr", "traffic": 1}],
            "candidates": ["not a tag"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 422


def test_coverage_plan_invalid_sample_header_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [
                {"accept_language": "en", "traffic": 1},
                {"accept_language": "fr;q=2", "traffic": 1},
            ],
            "candidates": ["fr-FR"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "samples", 1, "accept_language"]
    assert detail[0]["type"] == "accept_language.syntax_error"


def test_coverage_plan_default_not_in_supported_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "fr-FR",
            "samples": [{"accept_language": "fr", "traffic": 1}],
            "candidates": ["de-DE"],
            "max_additions": 1,
        }
    )
    assert response.status_code == 422


def test_coverage_plan_extra_field_rejected_422():
    response = _post_coverage(
        {
            "supported_languages": ["en-US"],
            "default_language": "en-US",
            "samples": [{"accept_language": "fr", "traffic": 1}],
            "candidates": ["fr-FR"],
            "max_additions": 1,
            "unexpected": 1,
        }
    )
    assert response.status_code == 422


def _post_change_impact(payload):
    return client.post("/api/v1/negotiate/change-impact", json=payload)


def _change_impact_payload(**overrides):
    payload = {
        "samples": [
            {"accept_language": "fr", "traffic": 100},
            {"accept_language": "en", "traffic": 200},
            {"accept_language": "de", "traffic": 50},
        ],
        "before": {
            "supported_languages": ["en-US", "zh-CN"],
            "default_language": "en-US",
        },
        "after": {
            "supported_languages": ["en-US", "zh-CN", "fr-FR"],
            "default_language": "en-US",
        },
    }
    payload.update(overrides)
    return payload


def test_change_impact_basic():
    response = _post_change_impact(_change_impact_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["total_traffic"] == 350.0
    assert body["changed_traffic"] == 100.0
    assert body["fallback_entered_traffic"] == 0.0
    assert body["fallback_exited_traffic"] == 100.0
    # Buckets are listed in first-appearance order across the samples.
    assert body["transitions"] == [
        {
            "before_selected_language": "en-US",
            "before_fallback": True,
            "after_selected_language": "fr-FR",
            "after_fallback": False,
            "changed": True,
            "traffic": 100.0,
        },
        {
            "before_selected_language": "en-US",
            "before_fallback": False,
            "after_selected_language": "en-US",
            "after_fallback": False,
            "changed": False,
            "traffic": 200.0,
        },
        {
            "before_selected_language": "en-US",
            "before_fallback": True,
            "after_selected_language": "en-US",
            "after_fallback": True,
            "changed": False,
            "traffic": 50.0,
        },
    ]
    # Bucket weights sum to the total; changed buckets to changed_traffic.
    assert sum(t["traffic"] for t in body["transitions"]) == body["total_traffic"]
    assert (
        sum(t["traffic"] for t in body["transitions"] if t["changed"])
        == body["changed_traffic"]
    )


def test_change_impact_enters_fallback_when_language_removed():
    payload = _change_impact_payload(
        samples=[{"accept_language": "zh", "traffic": 80}],
        after={"supported_languages": ["en-US"], "default_language": "en-US"},
    )
    response = _post_change_impact(payload)
    assert response.status_code == 200
    body = response.json()
    assert body["changed_traffic"] == 80.0
    assert body["fallback_entered_traffic"] == 80.0
    assert body["fallback_exited_traffic"] == 0.0
    bucket = body["transitions"][0]
    assert bucket["before_selected_language"] == "zh-CN"
    assert bucket["before_fallback"] is False
    assert bucket["after_selected_language"] == "en-US"
    assert bucket["after_fallback"] is True


def test_change_impact_default_switch_counts_as_changed():
    payload = _change_impact_payload(
        samples=[{"traffic": 10}],
        after={
            "supported_languages": ["en-US", "zh-CN"],
            "default_language": "zh-CN",
        },
    )
    response = _post_change_impact(payload)
    assert response.status_code == 200
    body = response.json()
    # Both sides fall back, but to different default languages.
    assert body["changed_traffic"] == 10.0
    assert body["fallback_entered_traffic"] == 0.0
    assert body["fallback_exited_traffic"] == 0.0
    bucket = body["transitions"][0]
    assert bucket["before_fallback"] is True
    assert bucket["after_fallback"] is True
    assert bucket["changed"] is True


def test_change_impact_locale_fallbacks_per_config():
    payload = _change_impact_payload(
        samples=[{"accept_language": "gsw", "traffic": 40}],
        before={
            "supported_languages": ["en-US", "zh-TW"],
            "default_language": "en-US",
            "locale_fallbacks": {"gsw": "zh-TW"},
        },
        after={
            "supported_languages": ["en-US", "zh-TW"],
            "default_language": "en-US",
        },
    )
    response = _post_change_impact(payload)
    assert response.status_code == 200
    body = response.json()
    # before: the gsw -> zh-TW chain is a normal hit; after (no chains):
    # the sample falls back to the default language.
    assert body["changed_traffic"] == 40.0
    assert body["fallback_entered_traffic"] == 40.0
    bucket = body["transitions"][0]
    assert bucket["before_selected_language"] == "zh-TW"
    assert bucket["before_fallback"] is False
    assert bucket["after_fallback"] is True


def test_change_impact_no_change_single_bucket():
    payload = _change_impact_payload(
        samples=[{"accept_language": "en", "traffic": 5}],
        after={
            "supported_languages": ["en-US", "zh-CN"],
            "default_language": "en-US",
        },
    )
    response = _post_change_impact(payload)
    body = response.json()
    assert body["changed_traffic"] == 0.0
    assert len(body["transitions"]) == 1
    assert body["transitions"][0]["changed"] is False


def test_change_impact_invalid_sample_header_422():
    payload = _change_impact_payload(
        samples=[
            {"accept_language": "en", "traffic": 1},
            {"accept_language": "fr;q=2", "traffic": 1},
        ]
    )
    response = _post_change_impact(payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "samples", 1, "accept_language"]
    assert detail[0]["type"] == "accept_language.syntax_error"


def test_change_impact_negative_traffic_422():
    payload = _change_impact_payload(
        samples=[{"accept_language": "fr", "traffic": -1}]
    )
    assert _post_change_impact(payload).status_code == 422


def test_change_impact_empty_samples_422():
    payload = _change_impact_payload(samples=[])
    assert _post_change_impact(payload).status_code == 422


def test_change_impact_config_validation_422():
    # before.default_language not in before.supported_languages.
    payload = _change_impact_payload(
        before={
            "supported_languages": ["en-US"],
            "default_language": "fr-FR",
        }
    )
    response = _post_change_impact(payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:2] == ["body", "before"]

    # after.locale_fallbacks dangling chain, located inside "after".
    payload = _change_impact_payload(
        after={
            "supported_languages": ["en-US", "zh-CN"],
            "default_language": "en-US",
            "locale_fallbacks": {"de": "xx", "xx": "yy"},
        }
    )
    response = _post_change_impact(payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "after", "locale_fallbacks"]
    assert "悬空" in detail[0]["msg"]


def test_change_impact_extra_field_rejected_422():
    payload = _change_impact_payload()
    payload["unexpected"] = 1
    assert _post_change_impact(payload).status_code == 422

    payload = _change_impact_payload()
    payload["before"]["unexpected"] = 1
    assert _post_change_impact(payload).status_code == 422
