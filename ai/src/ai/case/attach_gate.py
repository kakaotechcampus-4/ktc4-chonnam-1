"""통화 사례 첨부 관문: 대본이 보이스피싱 통화와 얼마나 같은지(0~1) 점수.

사례 검색은 정상 사례 없이 유사도 하나로 붙일지 정해서, 은행·민원 상담 같은 정상 통화에도
기관 사칭 사례가 붙었다. 관문은 정상 통화까지 학습한 로지스틱 회귀로 "사례를 붙일지"만 정한다.
판정(`decide_call`)·결론·근거에는 쓰지 않는다. 점수는 사기 확률이 아니다.

특징은 세 묶음이다(사람이 읽을 수 있게 적게 둔다).
- 문자 n-gram(2~4) TF-IDF: 정규화한 글(공백·기호 제거) 기준.
- 어절 TF-IDF: 소문자 한글·영숫자 덩어리.
- 수법 단계 신호(0/1): 신원 주장 종류, 요구 종류, 끊지 못하게 하는 말, 급박·비밀 유지, 구실.

학습은 ai/eval/voice_gate_train.py(sklearn, 오프라인)가 하고 계수와 어휘를 ai/kb/voice_gate.json 에
내보낸다. 운영 추론은 이 모듈의 순수 Python 이다(새 의존성 없음).
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ai.case.lexicon import (
    ACQUAINTANCE_PATTERN,
    BLOCKER_PATTERN,
    INSTITUTION_PATTERN,
    demand_kinds,
)
from ai.case.types import DemandKind
from ai.kb.normalize import normalize

MODEL_PATH = Path(__file__).resolve().parents[1] / "kb" / "voice_gate.json"
CHAR_NGRAM_SIZES: tuple[int, ...] = (2, 3, 4)
_WORD_RE = re.compile(r"[0-9a-z가-힣]+")

# 급박·비밀 유지·끊지 말라는 압박. 어휘표(lexicon)에 없는 신호라 여기 둔다.
PRESSURE_PATTERN = re.compile(
    r"비밀로|아무(?:한테|에게)도|말하지\s*마|알리지\s*마|끊지\s*마|끊으면|당장|급하|급해|시간이\s*없"
)
# 돈·정보를 요구하는 구실(사건 연루, 사고·납치, 저금리 대환, 안전 계좌).
PRETEXT_PATTERN = re.compile(
    r"연루|도용|대포\s*통장|피의자|구속|체포|영장|납치|합의금|저금리|대환|안전\s*계좌|보호\s*조치|자산\s*조사"
)
_MONEY = frozenset({DemandKind.MONEY_TRANSFER, DemandKind.CASH_WITHDRAWAL, DemandKind.GIFT_CARD})
_SECRET = frozenset({DemandKind.AUTH_CODE, DemandKind.PERSONAL_INFO})
_DEVICE = frozenset({DemandKind.APP_INSTALL, DemandKind.REMOTE_CONTROL, DemandKind.MOVE_MESSENGER})

STAGE_NAMES: tuple[str, ...] = (
    "identity_institution",
    "identity_acquaintance",
    "demand_money",
    "demand_secret",
    "demand_device",
    "blocker",
    "pressure",
    "pretext",
)


def stage_features(text: str) -> tuple[float, ...]:
    """수법 단계 신호. 화자 구분 없이 글 전체에서 찾는다(0 또는 1)."""
    demands = demand_kinds(text)
    flags = (
        INSTITUTION_PATTERN.search(text) is not None,
        ACQUAINTANCE_PATTERN.search(text) is not None,
        bool(demands & _MONEY),
        bool(demands & _SECRET),
        bool(demands & _DEVICE),
        BLOCKER_PATTERN.search(text) is not None,
        PRESSURE_PATTERN.search(text) is not None,
        PRETEXT_PATTERN.search(text) is not None,
    )
    return tuple(float(flag) for flag in flags)


def char_counts(text: str) -> Counter[str]:
    norm = normalize(text)
    return Counter(norm[i : i + n] for n in CHAR_NGRAM_SIZES for i in range(len(norm) - n + 1))


def word_counts(text: str) -> Counter[str]:
    return Counter(_WORD_RE.findall(text.lower()))


def tfidf(counts: Mapping[str, int], idf: Mapping[str, float]) -> dict[str, float]:
    """어휘에 있는 항목만 쓴다. TF 는 1+ln(tf), 묶음마다 L2 정규화."""
    weights = {term: (1.0 + math.log(count)) * idf[term] for term, count in counts.items() if term in idf}
    norm = math.sqrt(sum(w * w for w in weights.values()))
    if norm == 0.0:
        return {}
    return {term: w / norm for term, w in weights.items()}


@dataclass(frozen=True)
class GateModel:
    char_idf: dict[str, float]
    char_coef: dict[str, float]
    word_idf: dict[str, float]
    word_coef: dict[str, float]
    stage_coef: tuple[float, ...]
    intercept: float

    def decision(self, text: str) -> float:
        char_vec = tfidf(char_counts(text), self.char_idf)
        word_vec = tfidf(word_counts(text), self.word_idf)
        return (
            self.intercept
            + sum(w * self.char_coef[t] for t, w in char_vec.items())
            + sum(w * self.word_coef[t] for t, w in word_vec.items())
            + sum(c * x for c, x in zip(self.stage_coef, stage_features(text)))
        )

    def score(self, text: str) -> float:
        z = self.decision(text)
        # 큰 음수에서 exp 넘침을 피한다
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)


def model_from_dict(data: Mapping) -> GateModel:
    if tuple(data["stage_names"]) != STAGE_NAMES or tuple(data["char_ngram_sizes"]) != CHAR_NGRAM_SIZES:
        raise ValueError("voice gate model does not match feature definition")
    return GateModel(
        char_idf={t: v[0] for t, v in data["char"].items()},
        char_coef={t: v[1] for t, v in data["char"].items()},
        word_idf={t: v[0] for t, v in data["word"].items()},
        word_coef={t: v[1] for t, v in data["word"].items()},
        stage_coef=tuple(float(data["stage"][name]) for name in STAGE_NAMES),
        intercept=float(data["intercept"]),
    )


def model_to_dict(
    char: Mapping[str, tuple[float, float]],
    word: Mapping[str, tuple[float, float]],
    stage: Iterable[float],
    intercept: float,
    meta: Mapping,
) -> dict:
    return {
        **meta,
        "char_ngram_sizes": list(CHAR_NGRAM_SIZES),
        "stage_names": list(STAGE_NAMES),
        "intercept": intercept,
        "stage": dict(zip(STAGE_NAMES, stage)),
        "char": {t: list(v) for t, v in char.items()},
        "word": {t: list(v) for t, v in word.items()},
    }


_LOCK = threading.Lock()
_MODELS: dict[Path, GateModel] = {}


def load_model(path: Path = MODEL_PATH) -> GateModel:
    """프로세스 수명 동안 캐시한다. 동시에 들어온 첫 호출도 한 번만 읽는다."""
    with _LOCK:
        model = _MODELS.get(path)
        if model is None:
            model = model_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
            _MODELS[path] = model
        return model


def attach_score(transcript: str, path: Path = MODEL_PATH) -> float:
    """보이스피싱 같음 점수(0~1). 실패하면 예외를 그대로 올린다(호출하는 쪽이 미첨부로 처리)."""
    return load_model(path).score(transcript)
