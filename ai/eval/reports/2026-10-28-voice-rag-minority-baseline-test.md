# 통화 사례 검색 평가: minority-baseline-test

실행일: 2026-10-28 · 커밋: `315166d+dirty` · KB: 483건 · 분할 시드: `voice-rag-2026-10-28`

설정: 문자 n-gram (3,) TF-IDF 코사인, TF 원 빈도, KB 조각 없음(대본 전체), 질의 대본 전체, 정상 부착 상한 5.0%

질의: 평가셋 각 행의 `text`(대본 전체). 화자 분리 정보가 없어 상대 화자 발화만으로는 재지 못했다.

> 정상 통화는 모두 합성 대본이다. 정상 부착률을 실제 오탐률로 인용하지 않는다.

## 운영 지점

- 기준값: 0.1121 (dev 정상 통화 부착률 5.0% 이하가 되는 가장 낮은 값, 코드 값 `VOICE_MIN_SIMILARITY` = 0.0807)
- test hit@3: 43.9% (75/171, 95% 구간 36.6%~51.4%) — 목표 60.0% 미충족
- test 다수 유형 기준선: 48.0% (`institution_impersonation`)

## 분할별 지표

| 분할 | 보이스피싱 질의 | hit@1 | hit@3 | MRR | 다수 유형 기준선 | 정상 부착률 | AUC (top-1, 보이스피싱 대비 정상) | 유형 평균 hit@3 |
|---|---|---|---|---|---|---|---|---|
| dev | 173 (120 group) | 38.2% | 39.9% (69/173, 95% 구간 32.9%~47.3%) | 0.390 | 47.4% | 4.9% (2595건) | 0.802 | 27.0% |
| test | 171 (120 group) | 42.7% | 43.9% (75/171, 95% 구간 36.6%~51.4%) | 0.433 | 48.0% | 4.7% (2612건) | 0.782 | 29.4% |

## 유형별 hit@3 (건수)

소수 유형은 표본이 몇 건뿐이다. 합계 hit@3 은 기관 사칭·대출 빙자가 끌어올린 값이다.

| 유형 | KB | dev | test |
|---|---|---|---|
| account_recruitment | 36 | 0/16 | 0/17 |
| family_impersonation | 28 | 0/16 | 0/16 |
| institution_impersonation | 261 | 46/82 | 47/82 |
| kidnapping | 33 | 2/16 | 0/13 |
| loan_fraud | 87 | 15/28 | 22/27 |
| other_impersonation | 38 | 6/15 | 6/16 |

## 정상 부착률 분해

출처·종류·유형별 건수는 괄호 안이다. 출처·선택 부착률은 qa_written 을 뺀 선택 정상이 분모이고, 종류별·유형별은 qa_written 을 포함한 정상 전체가 분모다.

- dev: 출처별 실제 5.1%(2491), 합성 1.9%(104) / 종류별 call 6.7%(1741), monologue 1.3%(750), project 1.9%(104), qa_written 0.4%(509) / 유형별 bank_service 4.7%(1016), delivery 0.0%(14), family 11.8%(17), loan_consult 0.0%(5), other_normal 1.9%(1270), police_real 0.0%(14), public_office 7.3%(768) / 분모: 출처별은 qa_written 제외 선택 2595건, 종류별·유형별은 qa_written 포함 정상 전체 / qa_written 509건 부착 0.4%
- test: 출처별 실제 4.7%(2509), 합성 2.9%(103) / 종류별 call 5.8%(1759), monologue 2.3%(750), project 2.9%(103), qa_written 0.2%(491) / 유형별 bank_service 4.6%(1015), delivery 0.0%(14), family 10.5%(19), loan_consult 0.0%(8), other_normal 3.2%(1268), police_real 0.0%(11), public_office 4.4%(768) / 분모: 출처별은 qa_written 제외 선택 2612건, 종류별·유형별은 qa_written 포함 정상 전체 / qa_written 491건 부착 0.2%

## 지연

- 첫 호출(KB 로드 포함): 1825.3ms
- 질의 1건: p50 44.00ms · p95 142.40ms

## hit@3 실패 상위 10건 (test)

| id | 유형 | top-1 | 붙은 사례(유형) | 질의 앞부분 |
|---|---|---|---|---|
| korccvi-296 | loan_fraud | 0.1686 | VC-0281 (other_impersonation) | (실제 대본, 비공개) |
| synth2-k12-1 | kidnapping | 0.1521 | VC-0397 (family_impersonation) | 할머니 저 서아인데요, 흐흑. 할머니 살려 주세요. 아이고 우리 손주 왜 울어. 어디니? 모르는 아저씨들이 저를 차에 태웠어요. 무서워요 할머니… |
| synth2-k12-2 | kidnapping | 0.1514 | VC-0397 (family_impersonation) | 할머니 저 지안인데요, 흐흑. 할머니 살려 주세요. 아이고 우리 손주 왜 울어. 어디니? 모르는 아저씨들이 저를 차에 태웠어요. 무서워요 할머니… |
| korccvi-268 | loan_fraud | 0.1116 | VC-0348 (loan_fraud) | (실제 대본, 비공개) |
| korccvi-652 | institution_impersonation | 0.1107 | VC-0347 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-398 | institution_impersonation | 0.1087 | VC-0211 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-332 | institution_impersonation | 0.1070 | VC-0347 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-388 | institution_impersonation | 0.1063 | VC-0346 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-527 | institution_impersonation | 0.1029 | VC-0039 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-675 | loan_fraud | 0.1014 | VC-0289 (loan_fraud) | (실제 대본, 비공개) |
