import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ai.kb.normalize import normalize
from ai.kb.search import CASES_DIR, load_cases, search_cases
from ai.types import CategoryCode

DATASETS = Path(__file__).resolve().parents[1] / "eval" / "datasets"
# AI 는 BE 가 주소를 지운 본문만 받는다. 스킴(가린 hxxps:// 포함)·가린 점 [.]·www.·
# 스킴 없는 도메인(li**.cc/H***, infos-a*****ts.com)을 모두 주소로 본다.
ADDRESS_RE = re.compile(
    r"[a-z]{3,6}://|\[\.\]|www\.|"
    r"(?<![A-Za-z0-9*_-])[A-Za-z0-9*_-]+(?:\.[A-Za-z0-9*_-]+)*\.[A-Za-z*]{2,}(?![A-Za-z0-9*_-])",
    re.IGNORECASE,
)
# 위 검사는 최상위 도메인 목록 없이 모양만 본다. 목록(Public Suffix List)에 없는 끝부분이라
# 주소가 아닌 것으로 확인한 표기만 둔다. `.point` 는 최상위 도메인이 아니다.
NOT_DOMAINS = {"L.POINT"}
RRN_RE = re.compile(r"\d{6}[-\s]?\d{7}")
PHONE_RE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")


def _load_jsonl(name: str) -> list[dict]:
    path = DATASETS / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _kb_frontmatter() -> list[dict]:
    import yaml

    metas = []
    for path in sorted(CASES_DIR.glob("CE-*.md")):
        match = re.match(r"\A---\r?\n(.*?)\r?\n---", path.read_text(encoding="utf-8"), re.DOTALL)
        if match is not None:
            metas.append(yaml.safe_load(match.group(1)))
    return metas


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
        # 주소를 지우면 KB 레코드와 같아질 수 있다. 검색하는 message 도 본다.
        for text in (row["text"], row.get("message", row["text"])):
            assert normalize(text) not in kb_normalized, row.get("id", row["text"][:30])


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
        result = search_cases(row["message"])
        for match in result.matches:
            assert match.similarity < 0.6, row["text"][:30]


def test_every_curated_record_is_indexed():
    # frontmatter 가 YAML 로 안 읽히거나 variants 가 비면 _parse_case 가 조용히
    # 버린다. KB 건수가 줄어도 아무도 모르므로 curated 레코드 수와 맞춘다.
    curated = [meta for meta in _kb_frontmatter() if meta.get("status") == "curated"]

    assert len(load_cases(CASES_DIR)) == len(curated)


def _addresses(text: str) -> list[str]:
    return [found for found in ADDRESS_RE.findall(text) if found.upper() not in NOT_DOMAINS]


def test_kb_records_have_no_address():
    # 운영 검색 질의는 주소를 뺀 본문이다. KB 본문에 주소가 남으면 형식이 어긋난다.
    for case in load_cases(CASES_DIR):
        for variant in case.variants:
            assert not _addresses(variant), (case.case_id, variant[:40])


def test_search_datasets_carry_address_free_message():
    # 평가는 text(출처 원문)가 아니라 message(AI 가 받는 형태)로 검색한다.
    for name in ("rag_testset.jsonl", "benign.jsonl"):
        for row in _load_jsonl(name):
            label = (name, row.get("id", row["text"][:30]))
            if "exclude" in row:
                # 주소인지 판단을 보류한 행은 평가에서 빠진다. 사유는 비워 두지 않는다.
                assert row["exclude"].strip(), label
                continue
            assert not _addresses(row["message"]), label
            assert row["message"].strip(), label
            if not _addresses(row["text"]):
                # 주소가 없던 행은 손대지 않는다.
                assert row["message"] == row["text"], label


def test_near_dup_marks_point_to_kb_cases_on_smishing_rows():
    # near_dup_of 는 KB 에 거의 같은 사례가 있다는 사람 판정이다(기준은 datasets/README.md).
    # 정상 문자가 KB 사례와 닮은 것은 의도한 시험이라 표시하지 않는다.
    kb_ids = {case.case_id for case in load_cases(CASES_DIR)}
    marked = [row for row in _load_jsonl("rag_testset.jsonl") if "near_dup_of" in row]

    assert marked
    for row in marked:
        assert row["group"] == "smishing", row["id"]
        assert row["near_dup_of"] in kb_ids, row["id"]


def test_rag_testset_rows_carry_split():
    for row in _load_jsonl("rag_testset.jsonl"):
        assert row["split"] in {"dev", "test"}, row["id"]


def test_rag_testset_split_unit_stays_in_one_split():
    # 합성 정상 문자는 같은 템플릿끼리, 스미싱은 같은 게시물끼리 비슷하다.
    # 한 묶음이 dev 와 test 에 갈라지면 누수다. 행을 손으로 고칠 때도 지킨다.
    splits = defaultdict(set)
    for row in _load_jsonl("rag_testset.jsonl"):
        splits[row["template"] or row["source"] or row["id"]].add(row["split"])

    assert {key: values for key, values in splits.items() if len(values) > 1} == {}


def test_rag_testset_keeps_dev_and_test_smishing_per_category():
    counts = Counter(
        (row["category"], row["split"])
        for row in _load_jsonl("rag_testset.jsonl")
        if row["group"] == "smishing"
    )
    for code in CategoryCode:
        if code.value in {"other", "unknown"}:
            continue
        assert counts[(code.value, "dev")] >= 1, code.value
        assert counts[(code.value, "test")] >= 1, code.value


def test_kb_sources_do_not_overlap_rag_testset():
    # KB 로 옮긴 출처가 평가에 남으면 같은 게시물 문구로 자기 자신을 찾는다.
    kb_sources = {meta.get("source") for meta in _kb_frontmatter()} - {None}
    eval_sources = {row["source"] for row in _load_jsonl("rag_testset.jsonl") if row["source"]}

    assert kb_sources & eval_sources == set()
