# AI 평가 기준

브랜드·주장 목적·요구 행동 추출 정확성과 원문 근거, 피해 사례 검색 관련성, 설명의 관측 충실성, 실패 처리, 금지 출력, 비용·지연을 평가한다. 정상 알림과 스미싱 사례를 함께 사용한다.

RAG 유사도는 참고 정보다. 검색 품질과 최종 판정 품질을 분리하며 유사도를 악성 확률로 평가하거나 표시하지 않는다. 유사도·스캐너 점수·brands만 변경했을 때 최종 판정이 유지되는지 확인한다.

네 최종 상태는 스미싱 의심·공식 도메인 확인·판단 보류·분석 불가다. 진행 상태·입력 보완 요청과 구별한다. 구체적인 판정 조건·우선순위가 미확정인 케이스는 기대값을 임의로 정하지 않는다.

코드·데이터·모델·프롬프트·설정·KB·규칙 버전을 보존한다. 각 지표의 분모·평가법·통과 기준은 명시하며 미합의 수치와 미실행을 통과로 처리하지 않는다. 합성 모형의 결과를 실제 탐지 성능으로 표현하지 않는다.

격리 관측(Observations) 해석은 평가 대상이며, 적응형 보강 조사 비교만 이번 평가 범위 밖에 둔다. 과거 reports는 당시 기록으로 보존하고 새 평가 결과를 별도로 남긴다. 실행 명령은 구현 후 검증하여 추가한다.

## RAG 사례 검색 평가

`ai/` 에서 실행한다. 의존성 설치는 `ai/tests/README.md` 를 따른다.

```bash
python eval/rag_eval.py --label <방식>             # test 까지 재고 reports/ 에 리포트를 쓴다
python eval/rag_eval.py --label <방식> --dev-only  # 설정 조정용. dev 지표만 출력한다
```

Windows 에서 저장소 경로에 한글이 있으면 `.venv` 의 Python 이 `.pth` 를 cp949 로 읽다가
실패한다. 그때는 시스템 Python(3.11 이상)에 경로를 직접 넘긴다.

```bash
PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label <방식>
```

지표 정의, dev/test 분할, 운영 지점, 잠정 목표와 변경 규칙은
[설계 문서](../../docs/archive/superpowers/specs/2026-10-02-rag-eval-improvement-design.md)를 따른다.
설정값은 dev 에서만 조정하고 test 는 설정을 정한 뒤 한 번만 본다.

## 통화 사례 검색 평가

`ai/` 에서 실행한다. KB(`src/ai/kb/voice_cases/`)와 평가셋(`datasets/voice_rag.jsonl`)은 저장소 밖 Task 1
데이터셋에서 만든다.

```bash
python eval/voice_rag_build.py D:/voice/dataset/voice_calls.jsonl  # KB·평가셋 다시 만들기(분할 시드 고정)
python eval/voice_rag_eval.py --dev-only [--ngram 2,3 --tf log --chunk 800 --query-head 2000]  # 조정용 한 줄
python eval/voice_rag_eval.py --label <방식>                         # test 까지 재고 reports/ 에 리포트를 쓴다
```

hit@3 은 보이스피싱 질의의 상위 3개(기준값 이상) 중 같은 `type` 사례가 있는 비율이다. 기준값은 dev 정상
통화 부착률 5% 이하가 되는 가장 낮은 값이고, 정한 값을 `search.VOICE_MIN_SIMILARITY` 에 적는다.
조정 기록은 `reports/2026-10-28-voice-rag-tuning.md`.

### 사례 첨부 관문

`ai.case.attach_gate` 는 대본이 보이스피싱 통화와 같은지(0~1) 점수를 내고, `search.VOICE_GATE_MIN` 미만이면
사례를 붙이지 않는다(판정은 바꾸지 않는다). 학습은 sklearn 이 있는 시스템 Python 으로 한다(운영 .venv 에 없다).

```bash
<시스템 python> eval/voice_gate_train.py                      # src/ai/kb/voice_gate.json 다시 만들기(C=3, 결정적)
python eval/voice_gate_eval.py --extra-normals <로컬 정상 jsonl> --cache <저장소 밖 캐시> [--model <json>] [--contrast]
python eval/voice_rag_eval.py --dev-only --identity acquaintance --identity-mode score --gate  # 운영 관문 포함 한 줄
```

학습 음성은 `datasets/voice_gate_train.jsonl`(학습 전용 합성 정상, `D:/voice/scripts/gate_train.py` 가 만든다)이다.
결과는 `reports/2026-10-28-voice-rag-minority.md` "Task 6".
