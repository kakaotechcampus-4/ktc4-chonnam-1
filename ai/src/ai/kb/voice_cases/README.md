# 통화 사례(voice KB)

`search_voice_cases()`(`ai/kb/search.py`)가 검색하는 보이스피싱 통화 대본이다. 스미싱 `case_examples` 와
같은 색인 코드를 쓰고 KB 폴더와 `VOICE_*` 값만 다르다(스펙 D7). 유사도는 대본끼리 닮은 정도이며
보이스피싱 확률이 아니다. 판정에 쓰지 않는다.

## 만든 방법

Task 1 데이터셋 `D:\voice\dataset\voice_calls.jsonl`(저장소 밖)을 `ai/eval/voice_rag_build.py` 로 나눴다.

- `group`(같은 원본·같은 범죄자·같은 합성 템플릿) 단위로 KB 60% · dev 20% · test 20%. 유형마다 따로
  나누고 group 순서는 `sha256("voice-rag-2026-10-28:" + group)` 으로 정한다. 시드: `voice-rag-2026-10-28`.
- KB 에는 보이스피싱만 둔다. 정상 통화와 dev·test 는 `ai/eval/datasets/voice_rag.jsonl` 에 있다.
- 휴대폰 번호 모양은 `<전화번호>` 로 바꿨다. 주민번호·카드·계좌 번호는 Task 1 에서 이미 가렸다.
- 검수 없이 자동 등재했다(스펙 D5). `status: curated`, `reviewer: auto-import-2026-10-28`.
- 실제 대본(금감원 게시판·KorCCVi, 합성 아님)은 `licensed/` 하위 폴더와 `ai/eval/datasets/voice_rag_licensed.jsonl` 에
  둔다. 재배포 허락이 없어 `.gitignore` 로 저장소에서 뺐다. 공개 클론에는 합성 레코드만 있어 검색 결과가 로컬과 다르다.
  `search.case_paths()` 가 두 폴더를 파일 이름 순으로 함께 읽는다.

데이터셋을 바꾸면 빌드 스크립트로 KB 와 평가셋을 함께 다시 만들고 `voice_rag_eval.py` 로 기준값을 다시 정한다.
레코드를 손으로 고치지 않는다.

## 레코드 형식

`case_examples/CE-*.md` frontmatter 에 통화용 칸을 더했다.

| 칸 | 내용 |
|---|---|
| `type` | Task 1 유형(`institution_impersonation`·`loan_fraud`·`family_impersonation`·`kidnapping`·`account_recruitment`·`other_impersonation`·`other`) |
| `source` · `source_url` · `license` | 출처 이름, 게시물 URL(합성은 `null`), 이용 조건 문구 그대로 |
| `synthetic` · `origin` | 합성 대본이면 `true` · `synthetic`, 아니면 `false` · `web_public` |
| `dataset_id` · `group` | Task 1 의 `id` · `group`. 평가셋과의 누수 검사에 쓴다 |
| `variants` | 대본 전체 한 개. 자르지 않았다 |
| `normalized` | `normalize(variants[0])` |
| `categories` | 늘 `[]`. 문자 주제(`CategoryCode`)는 통화에 맞지 않는다. 유형은 `type` 에 있다 |

## 출처와 이용 조건

| 출처 | 이용 조건 요지 |
|---|---|
| 금융감독원 '바로 이 목소리'·'그놈 목소리' 게시판 | 홈페이지 저작권 정책상 **무단 복제·배포 금지**, 출처 표시 의무. 게시물에 재사용 금지 명시는 없음 |
| KorCCVi(숭실대) 보이스피싱 클래스 | 금감원 공개 녹음의 받아쓰기. 데이터는 재라이선스되지 않았고 재배포 자제 요청 |
| 자체 합성 대본 | 제한 없음 |

레코드마다 `license` 에 원문 문구를 남겼다. 이 폴더는 **원격 저장소에 올리기 전에 사용자 확인이 필요하다.**

## 한계

- KorCCVi 와 금감원 게시판은 같은 금감원 녹음을 다르게 받아쓴 것이 섞여 있을 수 있다. `group` 은 출처 안에서만
  묶었으므로 출처를 넘는 같은 녹음은 KB 와 평가셋에 갈라질 수 있다(평가가 낙관적일 수 있음).
- `matched_variant` 는 대본 전체다. 화면에 그대로 보이지 않는다.
- 소수 유형(가족·기타 사칭·계좌 모집·납치)은 사례가 4~14건이고 대부분 합성이다. 검색 성능은
  `ai/eval/reports/2026-10-28-voice-rag-*.md` 의 유형별 표를 본다.
