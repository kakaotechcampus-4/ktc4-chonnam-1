# 통화 사례 검색 평가: minority-adopted-test

실행일: 2026-10-28 · 커밋: `315166d+dirty` · KB: 483건 · 분할 시드: `voice-rag-2026-10-28`

설정: 문자 n-gram (3,) TF-IDF 코사인, TF 원 빈도, KB 조각 없음(대본 전체), 질의 대본 전체, 신원 acquaintance score +0.03, 관문 >= 0.89, 정상 부착 상한 5.0%

질의: 평가셋 각 행의 `text`(대본 전체). 화자 분리 정보가 없어 상대 화자 발화만으로는 재지 못했다.

> 정상 통화는 모두 합성 대본이다. 정상 부착률을 실제 오탐률로 인용하지 않는다.

## 운영 지점

- 기준값: 0.0807 (dev 정상 통화 부착률 5.0% 이하가 되는 가장 낮은 값, 코드 값 `VOICE_MIN_SIMILARITY` = 0.0807)
- test hit@3: 64.3% (110/171, 95% 구간 56.9%~71.1%) — 목표 60.0% 판단 보류
- test 다수 유형 기준선: 48.0% (`institution_impersonation`)

## 분할별 지표

| 분할 | 보이스피싱 질의 | hit@1 | hit@3 | MRR | 다수 유형 기준선 | 정상 부착률 | AUC (top-1, 보이스피싱 대비 정상) | 유형 평균 hit@3 |
|---|---|---|---|---|---|---|---|---|
| dev | 173 (120 group) | 60.1% | 63.0% (109/173, 95% 구간 55.6%~69.8%) | 0.616 | 47.4% | 4.9% (2595건) | 0.854 | 46.7% |
| test | 171 (120 group) | 61.4% | 64.3% (110/171, 95% 구간 56.9%~71.1%) | 0.627 | 48.0% | 5.7% (2612건) | 0.852 | 50.6% |

## 유형별 hit@3 (건수)

소수 유형은 표본이 몇 건뿐이다. 합계 hit@3 은 기관 사칭·대출 빙자가 끌어올린 값이다.

| 유형 | KB | dev | test |
|---|---|---|---|
| account_recruitment | 36 | 0/16 | 0/17 |
| family_impersonation | 28 | 6/16 | 7/16 |
| institution_impersonation | 261 | 68/82 | 66/82 |
| kidnapping | 33 | 6/16 | 8/13 |
| loan_fraud | 87 | 23/28 | 25/27 |
| other_impersonation | 38 | 6/15 | 4/16 |

## 정상 부착률 분해

출처·종류·유형별 건수는 괄호 안이다. 출처·선택 부착률은 qa_written 을 뺀 선택 정상이 분모이고, 종류별·유형별은 qa_written 을 포함한 정상 전체가 분모다.

- dev: 출처별 실제 5.1%(2491), 합성 0.0%(104) / 종류별 call 2.5%(1741), monologue 11.3%(750), project 0.0%(104), qa_written 0.0%(509) / 유형별 bank_service 0.8%(1016), delivery 0.0%(14), family 0.0%(17), loan_consult 0.0%(5), other_normal 8.0%(1270), police_real 0.0%(14), public_office 2.5%(768) / 분모: 출처별은 qa_written 제외 선택 2595건, 종류별·유형별은 qa_written 포함 정상 전체 / qa_written 509건 부착 0.0%
- test: 출처별 실제 5.9%(2509), 합성 0.0%(103) / 종류별 call 3.0%(1759), monologue 12.9%(750), project 0.0%(103), qa_written 0.0%(491) / 유형별 bank_service 1.2%(1015), delivery 0.0%(14), family 0.0%(19), loan_consult 0.0%(8), other_normal 9.2%(1268), police_real 0.0%(11), public_office 2.6%(768) / 분모: 출처별은 qa_written 제외 선택 2612건, 종류별·유형별은 qa_written 포함 정상 전체 / qa_written 491건 부착 0.0%

## 지연

- 첫 호출(KB 로드 포함): 1607.1ms
- 질의 1건: p50 42.67ms · p95 136.43ms

## hit@3 실패 상위 10건 (test)

| id | 유형 | top-1 | 붙은 사례(유형) | 질의 앞부분 |
|---|---|---|---|---|
| synth2-f02-2 | family_impersonation | 0.0778 | VC-0358 (family_impersonation) | 여보세요, 아빠? 나 현우인데 지금 영상통화로 바꿔도 돼? 그래, 무슨 일이야. 어? 화면이 왜 이렇게 흐려. 여기 신호가 약해서 그래. 아빠,… |
| korccvi-221 | institution_impersonation | 0.0762 | VC-0235 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-312 | institution_impersonation | 0.0738 | VC-0334 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-292 | institution_impersonation | 0.0724 | VC-0260 (institution_impersonation) | (실제 대본, 비공개) |
| korccvi-690 | institution_impersonation | 0.0720 | VC-0311 (institution_impersonation) | (실제 대본, 비공개) |
| synth2-o19-2 | other_impersonation | 0.0686 | VC-0376 (other_impersonation) | 우체국 국제특송 센터입니다. 고객님 앞으로 EMS 물품이 도착해 통관 대기 중입니다. 해외에서 받을 물건이 없는데요. 보낸 사람이 알렉스으로 되… |
| synth2-o19-1 | other_impersonation | 0.0683 | VC-0376 (other_impersonation) | 우체국 국제특송 센터입니다. 고객님 앞으로 EMS 물품이 도착해 통관 대기 중입니다. 해외에서 받을 물건이 없는데요. 보낸 사람이 존 스미스으로… |
| korccvi-90 | institution_impersonation | 0.0672 | VC-0040 (institution_impersonation) | (실제 대본, 비공개) |
| synth2-f12-1 | family_impersonation | 0.0668 | VC-0404 (family_impersonation) | 엄마, 나 서윤. 등록금 납부 때문에 전화했어. 응 지난주에 냈잖아. 또 뭐니? 학교 계좌가 바뀌었대. 처음 낸 건 반환 처리가 돼서 다시 내야… |
| synth2-f12-2 | family_impersonation | 0.0668 | VC-0404 (family_impersonation) | 엄마, 나 도윤. 등록금 납부 때문에 전화했어. 응 지난주에 냈잖아. 또 뭐니? 학교 계좌가 바뀌었대. 처음 낸 건 반환 처리가 돼서 다시 내야… |
