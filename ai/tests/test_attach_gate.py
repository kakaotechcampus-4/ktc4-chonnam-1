"""사례 첨부 관문(ai.case.attach_gate): 순수 Python 추론과 배포 모델·학습 데이터 계약."""

import json
import sys
from pathlib import Path

import pytest

from ai.case import attach_gate as gate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import voice_rag_build  # noqa: E402
from voice_gate_eval import PARITY_TEXTS, TRAIN_NORMALS  # noqa: E402

# eval/voice_gate_train.py 가 배포 모델을 만들 때 sklearn predict_proba 로 출력한 값.
# 모델을 다시 학습하면 그 출력으로 바꾼다(이 테스트는 sklearn 없이 돈다).
SKLEARN_SCORES = (
    0.9968786966,
    0.9118354759,
    0.2709970159,
    0.3041982779,
    0.7597242729,
)


def test_pure_python_score_matches_sklearn_to_six_decimals():
    model = gate.load_model()
    for text, expected in zip(PARITY_TEXTS, SKLEARN_SCORES, strict=True):
        assert abs(model.score(text) - expected) < 1e-6


def test_model_file_is_small_and_matches_feature_definition():
    assert gate.MODEL_PATH.stat().st_size <= 2_000_000
    data = json.loads(gate.MODEL_PATH.read_text(encoding="utf-8"))
    assert tuple(data["stage_names"]) == gate.STAGE_NAMES
    # 배포 가중치는 저장소에 둘 수 있는 자료(KB 보이스피싱 + 학습 전용 합성 정상)로만 만든다.
    assert set(data["trained_on"]) == {"positives", "synthetic_normals"}
    # 단계 신호는 모두 수법이라 정상 쪽 근거(음수 계수)가 되지 않게 학습한다.
    assert all(value >= 0 for value in data["stage"].values())


def test_mismatched_model_is_rejected():
    data = json.loads(gate.MODEL_PATH.read_text(encoding="utf-8"))
    data["stage_names"] = data["stage_names"][:-1]
    with pytest.raises(ValueError):
        gate.model_from_dict(data)


def test_stage_features_are_interpretable_flags():
    flags = dict(
        zip(
            gate.STAGE_NAMES,
            gate.stage_features("검찰 수사관입니다. 명의 도용 사건이라 비밀로 하시고 끊지 마세요. 인증번호 불러 주세요."),
        )
    )
    assert flags["identity_institution"] == 1.0
    assert flags["pretext"] == 1.0 and flags["pressure"] == 1.0 and flags["demand_secret"] == 1.0
    assert flags["identity_acquaintance"] == 0.0 and flags["blocker"] == 0.0
    assert gate.stage_features("") == (0.0,) * len(gate.STAGE_NAMES)


def test_tfidf_ignores_out_of_vocabulary_and_normalizes():
    vec = gate.tfidf({"가나": 3, "없음": 5}, {"가나": 2.0, "다라": 1.0})
    assert vec == {"가나": 1.0}
    assert gate.tfidf({"없음": 1}, {"가나": 1.0}) == {}


def test_gate_separates_obvious_cases():
    phishing, normal = PARITY_TEXTS[0], PARITY_TEXTS[2]
    assert gate.attach_score(phishing) > 0.9 > gate.attach_score(normal)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_gate_training_normals_are_train_only_and_disjoint_from_eval_and_kb():
    rows = _rows(TRAIN_NORMALS)
    assert rows and all(r["split"] == "train" and r["label"] == "normal" and r["synthetic"] for r in rows)
    groups_by_type: dict[str, set[str]] = {}
    for r in rows:
        groups_by_type.setdefault(r["type"], set()).add(r["group"])
        low, high = (1500, 5000) if r.get("kind") == "monologue" else (400, 2000)
        assert low <= len(r["text"]) <= high
        assert "AI Hub" not in r["text"] + r["source"] + r["license"]
        assert not voice_rag_build.MOBILE_RE.search(r["text"])
    for kind in ("family", "bank_service", "delivery", "public_office", "police_real", "other_normal"):
        assert len(groups_by_type[kind]) >= 12
    # 길고 한 사람만 말하는 독백형 정상(실제 정상 독백에 사례가 붙는 것을 줄이려고 더했다)
    monologues = [r for r in rows if r.get("kind") == "monologue"]
    assert len({r["group"] for r in monologues}) >= 12
    assert all({t["speaker"] for t in r["turns"]} == {"상대"} for r in monologues)
    eval_groups = {r["group"] for r in _rows(voice_rag_build.DATASET)}
    kb_text = "".join(p.read_text(encoding="utf-8") for p in voice_rag_build.VOICE_CASES_DIR.glob("VC-*.md"))
    train_groups = {r["group"] for r in rows}
    assert train_groups.isdisjoint(eval_groups)
    assert not any(f'group: "{g}"' in kb_text for g in train_groups)


def test_stored_feature_hash_matches_current_lexicon():
    # 어휘표(신원·끊지 말라는 말·요구 패턴)를 고치면 단계 신호 입력이 달라진다. 그땐 관문을 다시 학습하고
    # voice_gate_train.py 가 새 해시를 모델 json 에 적는다.
    data = json.loads(gate.MODEL_PATH.read_text(encoding="utf-8"))
    assert data["feature_hash"] == gate.feature_hash()


def test_model_from_dict_warns_on_lexicon_hash_mismatch(caplog):
    data = json.loads(gate.MODEL_PATH.read_text(encoding="utf-8"))
    data["feature_hash"] = "stale"
    with caplog.at_level("WARNING", logger=gate.LOGGER.name):
        gate.model_from_dict(data)
    assert "different lexicon" in caplog.text
