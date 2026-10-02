import json
import re
from pathlib import Path

from ai.kb.normalize import normalize
from ai.kb.search import CASES_DIR, load_cases, search_cases
from ai.types import CategoryCode

DATASETS = Path(__file__).resolve().parents[1] / "eval" / "datasets"
RRN_RE = re.compile(r"\d{6}[-\s]?\d{7}")
PHONE_RE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")


def _load_jsonl(name: str) -> list[dict]:
    path = DATASETS / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_kb_has_curated_cases():
    assert len(load_cases(CASES_DIR)) >= 20


def test_kb_stored_normalized_field_is_not_stale():
    # Case.normalized 는 variants 에서 계산되므로 그것과 비교하면 동어반복이다.
    # 검증 대상은 레코드에 **저장된** normalized 필드다. 사람이 variants 를 고치고
    # normalized 를 안 고치면 KB 문서가 코드와 어긋나는데, 그걸 여기서 잡는다.
    import yaml

    for path in sorted(CASES_DIR.glob("CE-*.md")):
        raw = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---", raw, re.DOTALL)
        if match is None:
            continue
        meta = yaml.safe_load(match.group(1))
        if meta.get("status") != "curated":
            continue
        assert meta["normalized"] == normalize(meta["variants"][0]), path.name


def test_kb_categories_are_known_and_single():
    # _parse_case 는 모르는 유형 코드를 조용히 버린다. 오타가 나면 검색 평가에서
    # hit 실패로만 보이므로 저장된 frontmatter 를 직접 검사한다. 테스트셋처럼
    # 대표 유형 하나만 붙인다. 여러 개를 허용하면 hit 이 부풀려진다.
    import yaml

    allowed = {code.value for code in CategoryCode} - {"other", "unknown"}
    for path in sorted(CASES_DIR.glob("CE-*.md")):
        raw = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---", raw, re.DOTALL)
        if match is None:
            continue
        categories = yaml.safe_load(match.group(1)).get("categories") or []
        assert len(categories) <= 1, path.name
        assert set(categories) <= allowed, path.name


def test_kb_records_are_deduplicated():
    # 한 레코드 안에서는 여러 변종이 같은 정규화 결과를 갖는 것이 정상입니다.
    # 금지되는 것은 서로 다른 레코드가 같은 변종을 나눠 갖는 것입니다.
    owner: dict[str, str] = {}
    for case in load_cases(CASES_DIR):
        for value in set(case.normalized):
            assert value not in owner, f"{case.case_id}와 {owner[value]}가 같은 변종"
            owner[value] = case.case_id


def test_datasets_exist_and_are_labelled():
    smishing = _load_jsonl("smishing.jsonl")
    benign = _load_jsonl("benign.jsonl")

    assert len(smishing) >= 20
    # 현재 확보된 정상 알림은 5건뿐이다. 목표는 20~30건이며 그때 이 값을 올린다.
    # n=5 로는 오탐률을 의미 있게 측정할 수 없다 — 그 한계를 README 에 적는다.
    assert len(benign) >= 5
    assert all(row["label"] == "smishing" for row in smishing)
    assert all(row["label"] == "benign" for row in benign)


def _pii_findings(text: str) -> list[str]:
    # 마스킹 대상 전부를 본다. 이 저장소는 public 이고 이것이 유일한 자동 방어다.
    found = []
    if RRN_RE.search(text):
        found.append("주민번호")
    if PHONE_RE.search(text):
        found.append("휴대폰")
    # 송장·운송장·주문번호. 공격자가 별표로 가린 가짜 번호는 \d 연속이 아니라 안 걸린다.
    if re.search(r"\d{8,}", text):
        found.append("8자리이상연속숫자")
    # URL 쿼리스트링에 식별자가 실려 나간다.
    if re.search(r"https?://\S+\?", text):
        found.append("URL쿼리")
    return found


def test_datasets_carry_no_obvious_personal_data():
    for name in ("smishing.jsonl", "benign.jsonl", "rag_testset.jsonl"):
        for row in _load_jsonl(name):
            assert not _pii_findings(row["text"]), (name, row["text"][:40])


def test_kb_records_carry_no_obvious_personal_data():
    # KB 레코드도 커밋된다. 데이터셋만 검사하면 296개가 무방비로 남는다.
    for case in load_cases(CASES_DIR):
        for variant in case.variants:
            assert not _pii_findings(variant), (case.case_id, variant[:40])


def test_eval_dataset_is_disjoint_from_kb():
    kb_normalized = {value for case in load_cases(CASES_DIR) for value in case.normalized}

    for row in _load_jsonl("smishing.jsonl") + _load_jsonl("rag_testset.jsonl"):
        assert normalize(row["text"]) not in kb_normalized, row.get("id", row["text"][:30])


def test_rag_testset_is_labelled():
    rows = _load_jsonl("rag_testset.jsonl")
    categories = {code.value for code in CategoryCode}
    assert len({row["id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["group"] in {"smishing", "benign", "hard_negative"}, row["id"]
        assert row["category"] in categories, row["id"]
        # 실물은 출처 URL 필수, 합성은 템플릿 ID 필수 — 실물 교체 추적용
        if row["origin"] == "web_public":
            assert row["source"], row["id"]
        else:
            assert row["origin"] == "synthetic_template" and row["template"], row["id"]


def test_benign_messages_do_not_match_kb_strongly():
    for row in _load_jsonl("benign.jsonl"):
        result = search_cases(row["text"])
        for match in result.matches:
            assert match.similarity < 0.6, row["text"][:30]
