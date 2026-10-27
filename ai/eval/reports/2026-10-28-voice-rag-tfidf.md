# 통화 사례 검색 평가: tfidf

실행일: 2026-10-28 · 커밋: `00cf6ac+dirty` · KB: 387건 · 분할 시드: `voice-rag-2026-10-28`

설정: 문자 n-gram (3,) TF-IDF 코사인, TF 원 빈도, KB 조각 없음(대본 전체), 질의 대본 전체, 정상 부착 상한 5.0%

질의: 평가셋 각 행의 `text`(대본 전체). 화자 분리 정보가 없어 상대 화자 발화만으로는 재지 못했다.

> 정상 통화는 모두 합성 대본이다. 정상 부착률을 실제 오탐률로 인용하지 않는다.

## 운영 지점

- 기준값: 0.0588 (dev 정상 통화 부착률 5.0% 이하가 되는 가장 낮은 값, 코드 값 `VOICE_MIN_SIMILARITY` = 0.0588)
- test hit@3: 88.6% (109/123, 95% 구간 81.8%~93.1%) — 목표 60.0% 충족
- test 다수 유형 기준선: 66.7% (`institution_impersonation`)

## 분할별 지표

| 분할 | 보이스피싱 질의 | hit@1 | hit@3 | MRR | 다수 유형 기준선 | 정상 부착률 | AUC (top-1, 보이스피싱 대비 정상) |
|---|---|---|---|---|---|---|---|
| dev | 126 (96 group) | 79.4% | 85.7% (108/126, 95% 구간 78.5%~90.8%) | 0.824 | 65.1% | 3.4% (58건) | 0.978 |
| test | 123 (96 group) | 85.4% | 88.6% (109/123, 95% 구간 81.8%~93.1%) | 0.870 | 66.7% | 13.6% (59건) | 0.952 |

## 유형별 hit@3 (건수)

소수 유형은 표본이 몇 건뿐이다. 합계 hit@3 은 기관 사칭·대출 빙자가 끌어올린 값이다.

| 유형 | KB | dev | test |
|---|---|---|---|
| account_recruitment | 12 | 0/4 | 4/5 |
| family_impersonation | 4 | 0/4 | 0/4 |
| institution_impersonation | 261 | 80/82 | 78/82 |
| kidnapping | 9 | 1/4 | 1/1 |
| loan_fraud | 87 | 27/28 | 26/27 |
| other_impersonation | 14 | 0/4 | 0/4 |

## 지연

- 첫 호출(KB 로드 포함): 1469.5ms
- 질의 1건: p50 16.52ms · p95 94.71ms

## hit@3 실패 상위 10건 (test)

| id | 유형 | top-1 | 붙은 사례(유형) | 질의 앞부분 |
|---|---|---|---|---|
| fss-18256 | institution_impersonation | 0.0559 | VC-0316 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-13 | loan_fraud | 0.0543 | VC-0214 (loan_fraud) | (실제 대본, 비공개) |
| korccvi-293 | institution_impersonation | 0.0520 | VC-0316 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-115 | institution_impersonation | 0.0509 | VC-0324 (institution_impersonation) | (실제 대본, 비공개) |
| synth-p-oth-health-2 | other_impersonation | 0.0479 | VC-0198 (institution_impersonation) | 국민건강보험공단 환급 담당입니다. 고객님께서 과다 납부하신 건강보험료 67만원이 환급 대상으로 확인되었습니다. 아 그래요? 환급받으시려면 계좌 … |
| synth-p-oth-health-1 | other_impersonation | 0.0478 | VC-0198 (institution_impersonation) | 국민건강보험공단 환급 담당입니다. 고객님께서 과다 납부하신 건강보험료 88만원이 환급 대상으로 확인되었습니다. 아 그래요? 환급받으시려면 계좌 … |
| synth-p-oth-health-3 | other_impersonation | 0.0478 | VC-0198 (institution_impersonation) | 국민건강보험공단 환급 담당입니다. 고객님께서 과다 납부하신 건강보험료 41만원이 환급 대상으로 확인되었습니다. 아 그래요? 환급받으시려면 계좌 … |
| synth-p-oth-health-4 | other_impersonation | 0.0477 | VC-0198 (institution_impersonation) | 국민건강보험공단 환급 담당입니다. 고객님께서 과다 납부하신 건강보험료 23만원이 환급 대상으로 확인되었습니다. 아 그래요? 환급받으시려면 계좌 … |
| korccvi-671 | account_recruitment | 0.0441 | VC-0197 (loan_fraud) | (실제 대본, 비공개) |
| korccvi-334 | institution_impersonation | 0.0416 | VC-0328 (institution_impersonation) | (실제 대본, 비공개) |
