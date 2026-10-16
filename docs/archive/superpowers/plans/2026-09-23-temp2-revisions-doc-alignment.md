# temp2 Revisions 문서 정합성 수정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `feature/scenario-message-test-ai-temp2`의 BE↔AI 결과 문서를 temp의 확정 계약과 일치시키고, 검증·커밋·푸시 후 PR #18에 변경 내용을 댓글로 남긴다.

**Architecture:** temp의 Revisions 요구사항과 실제 타입·조립 코드를 읽기 전용 기준으로 삼아 temp2의 설명 문서만 수정한다. 새 파이프라인 계약과 기존 화이트리스트 API를 구분하고, 다른 브랜치의 코드나 문서를 병합하지 않는다. JSON 예시와 허용 목록은 기준 커밋의 실제 타입으로 검증한다.

**Tech Stack:** Markdown, JSON, Git/GitHub CLI, 기존 Python 3.12·Pydantic 환경(문서 예시 검증용).

**Spec:** 기준 커밋 `9a85dfbf057e29f22f5b512d09113fa8787116a5`의 `docs/ai/Revisions.md`, `docs/ai/Revisions-handoff.md`, `ai/src/ai/types.py`, `ai/src/ai/pipeline/results.py`, `ai/src/ai/pipeline/analysis.py`. PR #17은 기준 구현, PR #18은 수정 대상이다.

## Global Constraints

- 작업 브랜치는 `feature/scenario-message-test-ai-temp2`, PR #18의 base는 `feature/scenario-message-test-ai`로 유지한다.
- 변경 파일은 이 계획과 `docs/ai-be-final-result-schema.md` 두 문서로 제한한다. 코드·테스트·의존성·기존 정책 문서는 변경하지 않는다.
- PR #18은 Draft를 유지한다. 병합·배포·강제 푸시는 수행하지 않는다.
- 기준: “최종 `result`는 항상 `url.official`과 같다.”
- 기준: “LLM, RAG 유사도, 메시지·격리 환경 분석 결과가 이 값을 뒤집지 않는다.”
- 기준: “`answer=null`은 `official=true`인 조기 반환에만 사용한다.”
- 기준: “한 분석이 실패해도 다른 분석에서 확보한 결과는 보존한다.”
- 기준: “HTML만 제공한 입력의 설명은 요소 존재 범위를 넘어서 실제 화면 노출·다운로드·폼 제출을 확인했다고 주장하지 않는다.”
- 기존 CLAUDE/ADR의 화이트리스트 설명은 기존 API의 정책으로 남기고, 이 문서가 설명하는 새 파이프라인의 score 기반 boolean과 혼합하지 않는다.
- temp2에는 Revisions 문서와 새 코드가 없으므로 기준 자료 링크는 위 커밋의 GitHub permalink를 사용한다. 존재하지 않는 상대 경로를 추가하지 않는다.
- 커밋 제목과 PR 댓글은 한국어로 작성한다. PR 댓글 게시 전 검증 결과와 실제 수정 커밋을 확보한다.

## Review Focus

1. 두 `answer=true`여도 `official=false`이면 최종 결과가 false임을 전체 응답 예시와 결정표로 확인한다.
2. 분석 실패가 먼저 발생해도 `official=true`이면 객체·키를 유지한 말단 null 10개가 우선함을 예시와 타입 검증으로 확인한다.
3. 빈 문자열, `unknown`, `없음`, `null`을 혼동하지 않고, 실패·부분 분석에서 이미 확인한 분류와 근거를 보존하는지 확인한다.
4. 배송 조회 문자와 일반 로그인 폼을 서로 다른 doubt로 보존하며, 차이나 로그인 폼 자체를 의심 신호로 확정하지 않는지 확인한다.
5. 성공한 페이지 입력과 수집 실패의 Python 호출 계약을 구분하고, 임의의 원격 JSON envelope나 아직 없는 BE 구현을 문서가 확정하지 않는지 확인한다.

## 파일과 근거

| 파일 | 역할 |
|---|---|
| `docs/ai-be-final-result-schema.md` | 수정 대상. 새 BE↔AI 입력·출력 계약과 해석 기준을 설명한다. |
| 이 계획 파일 | 수정 범위, 기준 커밋, 검증·게시 순서를 기록한다. |
| 기준 커밋의 Revisions/인계 문서 | 제품 정책과 BE·수집기·FE 책임의 근거다. |
| 기준 커밋의 types/pipeline 코드 | 실제 필드·Enum·검증·호출 형태의 근거다. |

현재 temp2는 `558d8507e0eaa68d48a7fe8f8c9920dcaa4ebc65`이며 PR #18의 기존 변경은 결과 구조 문서 한 파일이다. 현재 저장소의 이 문서 참조 검색에서는 함께 고쳐야 할 다른 직접 참조 문서가 발견되지 않았다.

## 수정 대응표

| 기존 설명 | 수정할 계약 |
|---|---|
| 화이트리스트 일치 여부인 official | BE가 score를 가공한 엄격한 boolean. AI는 원점수·임계값을 받지 않는다. |
| official OR answer·브랜드·분야 비교 | `result = url.official`. 비교·유사도·개별 answer가 뒤집지 않는다. |
| URL/domain 미확인 시 빈 문자열 | 공백뿐인 값도 거부한다. BE가 검증하고 입력 오류를 처리한다. AI는 형식을 재작성하지 않는다. |
| answer가 true이면 details를 비움 | 완료한 분석의 분류·근거를 제공한다. doubt는 의심 신호와 별개다. |
| 미확인은 빈 문자열, 실패 표현은 미정 | 정형 값은 `unknown`, 확인된 요구/요소 부재는 `없음`, 조기 반환만 `null`. 실패 answer는 false다. |
| 동일 doubt 목록으로 비교 | MessageDoubt 13개+없음+unknown, EnvDoubt 7개+없음+unknown으로 분리한다. |
| KB국민은행 등 임의 브랜드/옛 category 목록 | 기준 구현의 Brand 23개+unknown, Topic 7개+unknown을 그대로 싣는다. 목록 밖 브랜드를 임의 추가하지 않는다. |
| 로그인 UI·분류 불일치를 의심 확정 | 일반 로그인과 분류 차이 자체는 위험 신호가 아니다. 현재 출처의 실제 요청 근거를 검증한다. |
| reason 전체를 원문과 비교, 불일치면 true | 원문 인용/HTML 요소 근거를 검증하고 설명은 결정적 평문으로 만든다. 실패나 다른 검증된 신호를 지워 true로 바꾸지 않는다. |
| 격리 서버가 env 완성 결과를 반환 | 수집기가 `{brand, category, info}` 자료를 제공하고 AI가 env를 만든다. info는 HTML이며 실행하지 않는다. |
| 화이트리스트 즉답과 같은 조기 반환 시점 | 유효한 score 기반 boolean 도착 이후 조립한다. BE가 병렬 작업·취소·늦은 결과·콜백을 관리한다. |

---

### Task 1: 결과 구조 문서를 현재 계약으로 수정하고 검증

**Files:**
- Modify: `docs/ai-be-final-result-schema.md` 전체 1–316행.
- Read: 위 기준 커밋의 다섯 Spec 파일.
- Validation: Markdown 안 JSON 예시의 구문·타입·분기 검증. 제품 테스트 파일은 추가하지 않는다.

**Interfaces:**
- Consumes: `UrlAnalysis(final_url, domain, official)`, `IsolatedPage(brand, category, info)`, `Brand`, `Topic`, `MessageDoubt`, `EnvDoubt`, `AnalysisResponse`.
- Produces: 새 계약을 설명하는 독립 문서와 검증 결과. Task 2는 이 문서의 수정 커밋과 검증 결과를 댓글 근거로 사용한다.

- [x] **Step 1: 기준 자료와 기존 문서 차이를 확인한다.**

```powershell
git show 9a85dfbf057e29f22f5b512d09113fa8787116a5:docs/ai/Revisions.md
git show 9a85dfbf057e29f22f5b512d09113fa8787116a5:docs/ai/Revisions-handoff.md
git show 9a85dfbf057e29f22f5b512d09113fa8787116a5:ai/src/ai/types.py
git show 9a85dfbf057e29f22f5b512d09113fa8787116a5:ai/src/ai/pipeline/results.py
git show 9a85dfbf057e29f22f5b512d09113fa8787116a5:ai/src/ai/pipeline/analysis.py
```

수정 대응표의 모든 행을 원문과 대조한다. 이미 구현된 타입과 아직 운영 검증이 필요한 모델 정확도·실서비스 연동을 구별한다.

- [x] **Step 2: 문서의 설명 구조를 다음 순서로 교체한다.**

1. 적용 범위와 기준 커밋/PR 링크: 새 `ai.pipeline` 계약이며 legacy 정책 변경을 뜻하지 않는다.
2. 입력: URL을 제거한 본문, 검증된 UrlAnalysis, HTML을 담은 IsolatedPage. 세 공개 함수의 실제 시그니처를 표로 싣는다.
3. 전체 응답: 외부 키는 `url`, `message`, `env`, `result`이며 두 part는 공통 구조지만 출처와 doubt 목록은 독립이다.
4. 결정표: official true/false와 완료·위험·실패 조합을 설명한다. true에서는 두 part의 다섯 말단 값씩 모두 null이다.
5. 분류 목록: 구현 Enum 전체를 값 그대로 싣는다. Topic은 택배/쇼핑/금융/공공기관/의료·건강/보안/선물·이벤트/unknown이다.
6. 근거와 실패: 현재 출처, RAG 참고 자료, 요청/완료/부정 구분, 부분 결과 보존, 결정적 평문 reason을 설명한다.
7. 연동 책임: BE 검증·병렬 실행·취소·직렬화·콜백, 수집기의 실패 전달, FE의 의심/악성 및 null/unknown/없음 구분을 명시한다.

`score <= T`는 BE가 true로 가공하고 같은 점수도 포함한다. T 및 실제 score 필드 경로, 누락·조회 실패·범위 초과 처리는 BE에서 확정해야 하며 문서에서 새 기본값을 만들지 않는다.

- [x] **Step 3: 유효한 JSON 예시를 넣는다.**

모든 데이터 예시는 주석 없는 `json` 코드 블록을 사용한다. 다음 다섯 가지를 포함한다.

- URL 입력: `{"final_url":"https://example.com/track","domain":"example.com","official":false}`.
- 페이지 입력: `{"brand":"unknown","category":"unknown","info":"<form><label>아이디<input name=\"username\"></label><label>비밀번호<input type=\"password\"></label></form>"}`.
- 전체 false 응답: URL official=false, message의 category=택배/doubt=배송 조회/answer=true, env의 doubt=로그인·인증 입력폼/answer=true, result=false. 두 출처의 reason을 따로 작성한다.
- 전체 true 응답: 유효 URL official=true, message/env 객체와 details 키를 유지하고 말단 값 10개는 모두 null, result=true.
- 실패 env: brand/category/doubt=unknown, answer=false, reason은 실제 수집 실패 사실을 설명한다. 이미 확인한 정보가 있으면 보존한다는 별도 설명을 붙인다.

reason 예시는 의미 설명용이며 실제 모델 출력이나 수집 성공을 주장하지 않는다. 타입 허용 목록에 없는 브랜드·분야를 예시로 넣지 않는다.

- [x] **Step 4: 문서 예시를 기준 구현의 실제 타입으로 검증한다.**

아래 Python을 PowerShell here-string으로 표준입력에 전달한다. 현재 temp2 코드나 작업 트리를 바꾸지 않고 기준 커밋의 types 모듈만 메모리에서 읽는다.

```python
import json, re, subprocess, sys, types
from pathlib import Path

ref = '9a85dfbf057e29f22f5b512d09113fa8787116a5'
source = subprocess.check_output(
    ['git', 'show', f'{ref}:ai/src/ai/types.py'], encoding='utf-8')
module = types.ModuleType('revision_document_contract')
sys.modules[module.__name__] = module
exec(compile(source, f'{ref}:types.py', 'exec'), module.__dict__)
doc = Path('docs/ai-be-final-result-schema.md').read_text(encoding='utf-8')
examples = [json.loads(block) for block in re.findall(
    r'```json\s*\n(.*?)\n```', doc, re.S)]
counts = {'url': 0, 'page': 0, 'response': 0, 'failure': 0}
branches = set()
for data in examples:
    if set(data) == {'url', 'message', 'env', 'result'}:
        response = module.AnalysisResponse.model_validate(data)
        assert response.model_dump(mode='json') == data
        branches.add(data['result'])
        counts['response'] += 1
        if data['result']:
            leaves = [part[key] for part in (data['message'], data['env'])
                      for key in ('brand', 'category', 'answer')]
            leaves += [value for part in (data['message'], data['env'])
                       for value in part['details'].values()]
            assert len(leaves) == 10 and all(x is None for x in leaves)
        else:
            assert data['message']['answer'] is True
            assert data['env']['answer'] is True
    elif set(data) == {'final_url', 'domain', 'official'}:
        module.UrlAnalysis.model_validate(data)
        counts['url'] += 1
    elif set(data) == {'brand', 'category', 'info'}:
        module.IsolatedPage.model_validate(data)
        counts['page'] += 1
    else:
        module.EnvironmentPart.model_validate(data)
        assert data['answer'] is False
        counts['failure'] += 1
assert branches == {False, True}
assert all(counts.values()), counts
for name in ('Brand', 'Topic', 'MessageDoubt', 'EnvDoubt'):
    values = [item.value for item in getattr(module, name)]
    assert all(f'`{value}`' in doc for value in values), (name, values)
print('JSON/type/branch/enum presence checks passed:', counts)
```

실행 환경: `$env:PYTHONDONTWRITEBYTECODE='1'`; here-string을 `& 'ai/.venv/revisions/Scripts/python.exe' -X utf8 -`로 파이프한다. `$OutputEncoding`은 UTF-8로 설정한다. 출력과 종료 코드 0을 확인한다. Enum 검사는 누락 점검이므로 각 표에 잘못된 값이 추가되지 않았는지도 수동 대조한다.

- [x] **Step 5: 문서와 변경 범위를 독립 리뷰한다.**

Review Focus 다섯 항목, 기준 커밋 permalink, 코드와 미배포 연동의 구분을 확인한다. 기존 화이트리스트 API 설명은 그대로 두되 새 문서에서 옛 정책을 현재 규칙으로 안내하지 않는지 확인한다. 일반 로그인/결제 UI나 분류 차이가 위험 신호를 자동 생성한다는 설명이 없어야 한다.

```powershell
git diff --check
git diff --stat
git diff --name-only
```

문서만 수정하므로 제품 테스트를 새로 만들거나 전체 AI 테스트 605개를 재실행할 필요는 없다. 이전 구현의 테스트 결과를 이번 문서 수정의 신규 실행 결과로 주장하지 않는다.

- [x] **Step 6: 문서 두 파일만 한국어 커밋한다.**

```powershell
git add -- docs/ai-be-final-result-schema.md docs/superpowers/plans/2026-09-23-temp2-revisions-doc-alignment.md
git diff --cached --check
git commit -m 'docs(ai): 결과 구조 문서를 Revisions 계약에 맞춰 수정'
```

### Task 2: PR #18에 수정 커밋과 검증 내용을 게시

**Files:** Read `docs/ai-be-final-result-schema.md`; PR 댓글 본문은 저장소 밖 임시 UTF-8 파일로 작성한다.

**Interfaces:**
- Consumes: Task 1의 검토 완료 문서, 커밋 SHA, 문서 검증 출력.
- Produces: temp2 원격에 반영된 커밋과 PR #18 댓글 URL.

- [ ] **Step 1: 원격 상태와 PR의 head/base를 재확인한다.**

```powershell
git fetch origin feature/scenario-message-test-ai-temp2
git rev-list --left-right --count origin/feature/scenario-message-test-ai-temp2...HEAD
gh pr view 18 --repo kakaotechcampus-4/ktc4-chonnam-1 --json headRefName,baseRefName,isDraft,state
```

원격만의 새 커밋이 있으면 내용을 확인해 충돌을 해결한다. 강제 푸시하지 않는다. head=temp2, base=feature/scenario-message-test-ai, Draft=true를 유지한다.

- [ ] **Step 2: 현재 브랜치를 일반 푸시한다.**

```powershell
git push origin feature/scenario-message-test-ai-temp2
```

- [ ] **Step 3: 수정 내용 댓글을 작성해 게시한다.**

댓글에는 실제 수정 커밋 링크, `official/result` 고정 규칙, 말단 null 10개와 실패 보존, 분리된 Enum, 입력·근거·연동 설명 및 실제 문서 검증 결과를 짧게 적는다. 기존 PR 본문의 차이점은 최초 제출 당시 상태였으며 해당 수정으로 해소됐음을 명시한다. PR 본문·제목·Draft 상태는 별도 변경하지 않는다.

```powershell
gh pr comment 18 --repo kakaotechcampus-4/ktc4-chonnam-1 --body-file $commentPath
```

`$commentPath`는 실제 본문을 UTF-8 BOM 없이 기록한 임시 파일의 절대 경로다. 커밋 SHA와 검증 결과를 얻은 뒤 작성하며 추정값을 게시하지 않는다.

- [ ] **Step 4: 게시 결과를 확인한다.**

```powershell
gh pr view 18 --repo kakaotechcampus-4/ktc4-chonnam-1 --json url,headRefOid,headRefName,baseRefName,isDraft,comments,files
git status --short
```

원격 head가 수정 커밋과 일치하고 댓글이 게시됐는지 확인한다. 최종 보고에는 계획 링크, 수정 커밋, 검증 결과, 댓글 링크를 남긴다.

## 계획 자체 검토

- 기존 문서의 입력·출력·결정식·어휘·미정 항목을 수정 대응표와 Task 1에 모두 연결했다.
- Review Focus 다섯 항목은 Task 1의 타입/분기 검증 또는 독립 문서 리뷰로 검증한다.
- 타입과 함수명은 기준 커밋의 실제 구현을 사용한다. 새 API나 BE 구현을 제안하지 않는다.
- PR 댓글 게시는 사용자 요청에 포함된다. 이 계획 작성 단계에서는 원문 수정·푸시·댓글 게시를 실행하지 않는다.
