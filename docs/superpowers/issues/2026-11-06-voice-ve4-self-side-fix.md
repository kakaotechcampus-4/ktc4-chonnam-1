# [AI] 통화 녹음이 VE4(사칭·요구 없음)로 잘못 나오는 문제

## 무엇

카드 배송 사칭 녹음(`녹음_2026-11-03_214211.mp3`, 54초)을 넣으면 VE4 "🎙️ 녹음에 사칭·요구하는 말이 없어요"가 나온다. 녹음은 "우체국 배송기사입니다"로 시작해 카드 오배송·개인정보 유출을 핑계로 "카드사 대표번호를 불러줄 테니 전화하시라"고 유도하는 전형적인 사칭 통화다.

STT·LLM 문제가 아니다. `verify_call()`이 찾은 신원·요구를 전부 받는 사람 말(`self_side`)로 버려 근거가 0개가 되고, `decide_call()` U-5 분기가 `input_required`를 내서 VE4가 나간다.

운영에서 이렇게 된 직접 원인은 `116f7dc`(kiki03st)가 밝히고 고쳤다. 운영 whisper 전사문(259자)에 문장 부호가 끝 물음표 하나뿐이라 통화 전체가 한 문장이 됐고, 읽은 주장 7개가 모두 `self_side`로 버려졌다. `116f7dc`는 말 끝 어미 뒤에서도 문장을 자른다.

이 문서는 그 위에 남은 두 가지를 고친다. 문장을 제대로 잘라도 상대가 자기 지시를 되풀이한 "전화하시라고요"는 되묻기로 버려진다(②). 그리고 꼴 때문에 주장을 다 버린 경우에도 VE4가 나간다(③).

## 재현

`f39da9c`(11-04)에서 로컬 whisper-large-v3-turbo로 받아쓴 글을 `split_sentences()`·`mask_pii()`로 나눠 `analyze_call()`에 넣었다. 같은 문장을 지금 코드(`67c3fac`)로도 돌렸다. 지금 코드에서는 운영 전사문처럼 문장 부호를 끝 물음표 하나만 남긴 판도 넣었다. 읽기 결과는 세 가지로 넣었다: 어휘표 폴백, 어휘표 결과를 `ok`로, LLM이 인용을 정확히 뽑은 결과.

| 코드 | 결론 (세 가지 읽기 모두) | LLM이 정확히 뽑았을 때 버려진 항목 |
| --- | --- | --- |
| `f39da9c` (`732b994` 직전) | `input_required` | 신원·요구 (`self_side`) |
| `67c3fac` (현재, ②③ 전) | `inconclusive` + `caution` | 요구 (`self_side`) |
| `67c3fac` + ②③ | `inconclusive` + `caution` | 없음 |

- 지금 코드에서 신원이 인정되는 건 `732b994`(11-04)에서 우체국을 기관 낱말에 넣었기 때문이다.
- 어휘표 폴백은 LLM 시간 초과·오류 때와 같은 경로다. `1572e24`(11-06) 이후로는 폴백이면 U-5를 건너뛴다.
- `f39da9c`는 문장 부호가 있는 판만 돌렸다. 지금 코드는 두 판의 결과가 같았다.
- 이 문서에 옮긴 문장만 넣은 결과다. 54초 전체 대본과 운영 전사문으로는 돌리지 않았다.

`f39da9c`에서 버려진 항목은 아래와 같고 사유는 전부 `self_side`다.

| 버려진 항목 | 들어 있던 문장 | 원인 |
| --- | --- | --- |
| 신원 "우체국 배송기사입니다" | "네 여보세요 안녕하세요 우체국 배송기사입니다 네 네 기사님 맞으시죠?" | ① 턴이 붙은 문장의 끝 `?` (지금은 버려지지 않음) |
| 요구 "불러줘서 전화하시라고요" | "아 불러줘서 전화하시라고요." | ② "~라고요"를 되묻기로 봄 |
| 명분 "개인정보 요출때문에" | "…제가 러태카드 대표 번호 제가 불러줄게요 지금 메모가 될까요?" | 이번 범위 밖 |
| 요구 "집주소가 그러면 영동포인가요" | "선생님 집주소가 그러면 영동포인가요?" | 이번 범위 밖 |

## 고칠 곳

②가 남은 직접 원인이고 ③은 같은 일이 생겨도 잘못된 안심 문구가 나가지 않게 막는 안전망이다. ①은 `116f7dc`로 대체했다.

### ① 문장 자르기 — `116f7dc`로 대체

처음에는 "~니다" 뒤 공백에서 자르는 안을 넣었다. 같은 날 `116f7dc`가 "~요·~다·~죠·~까" 뒤에서 자르도록 더 넓게 고쳐서 이쪽은 뺐다.

남은 일이 하나 있다. `116f7dc`는 "검찰입니다 라고 하더라고요"를 "검찰입니다" / "라고 하더라고요"로 자른다. 잘린 "검찰입니다"는 `asserts_institution()`이 자기소개로 보고 `is_recipient_side()`도 거르지 않는다. 받는 사람이 옮긴 말이 상대의 기관 사칭 근거로 쓰일 수 있다. 처음 안은 뒤에 "라고·라며·하고·하더·하면서·그러"가 오면 자르지 않았다. 이 예외를 `116f7dc`에 붙일지는 kiki03st와 따로 정한다.

### ② `ai/src/ai/case/forms.py` — `_QUOTATION`

지금은 "~라고요·~다고요"를 모두 옮긴 말·되묻기로 본다. 그래서 사기범이 자기 지시를 되풀이한 "전화하시라고요"도 받는 사람 말로 빠진다.

첫 패턴 앞에 `(?<!시)`를 붙인다. "-시라고요"는 듣는 사람을 높인 꼴이라 상대가 한 말이다. 받는 사람이 되묻는 "송금하라고요?"에는 '시'가 없어 기존 동작이 그대로다.

```diff
 _QUOTATION = re.compile(
-    r"(?:(?<!더)라|다|냐|자)\s?(?:고|구)"
+    r"(?<!시)(?:(?<!더)라|다|냐|자)\s?(?:고|구)"
     r"(?=요|[?？]|\s*$|\s+(?:했|하셨|하시더|하시던|하던|하더|하는|그러|그랬|말|돼|되어|되있|되더))"
```

### ③ (안전망) `ai/src/ai/case/gate.py` — `decide_call()` U-5 분기

지금은 읽기가 성공했는데 통과한 근거가 0개면 `input_required`다. "아예 못 찾음"과 "찾았는데 꼴 때문에 다 버림"이 같은 VE4 문구로 나간다.

`self_side`로 버린 신원·요구가 있으면 `inconclusive`(🤔 판단하기 어려워요)로 넘긴다. `not_in_source`·`too_short` 같은 다른 사유는 그대로 `input_required`다.

```diff
     if (
         reading_status != "fallback"
         and not link_spoken
         and not (codes & _ANY_DEMAND)
         and not (codes & {_E.INSTITUTION_CLAIM, _E.ACQUAINTANCE_CLAIM})
+        # 꼴 때문에 버린 신원·요구가 있으면 "말이 없다"고 단정하지 않는다
+        and not any(
+            r.reason == "self_side" and r.kind in ("identity", "demand")
+            for r in verify.rejected
+        )
     ):
```

## 추가할 테스트

새 테스트 5개 중 4개는 수정 전 코드에서 실패하고 수정 후 통과한다. 나머지 1개(`not_in_source`)는 기존 동작을 지키는 테스트라 수정 전에도 통과한다.

`ai/tests/test_case_forms.py`

```python
@pytest.mark.parametrize("sentence", ["아 불러줘서 전화하시라고요.", "그 번호로 전화하시라고요"])
def test_honorific_rago_is_caller_side(sentence):
    # "-시라고요" 는 듣는 사람을 높인 꼴이라 상대가 자기 지시를 되풀이한 말이다.
    # 받는 사람이 되묻는 "송금하라고요?" 에는 '시' 가 없다 (test_quotation)
    assert not forms.is_quotation(sentence)
    assert not forms.is_recipient_side(sentence)
```

`ai/tests/test_case_gate.py` (`from ai.case.types import (...)`에 `RejectedClaim` 추가)

```python
def test_u5_self_side_rejections_are_not_input_required():
    # 꼴 때문에 버린 신원·요구가 있으면 "사칭·요구하는 말이 없다"고 단정하지 않는다
    v = _verify().model_copy(
        update={"rejected": [RejectedClaim(kind="demand", quote="전화하시라고요", reason="self_side")]}
    )
    r = _decide(v)
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is False
    assert r.stop_actions == [S.HANG_UP]


def test_u5_not_in_source_rejections_stay_input_required():
    v = _verify().model_copy(
        update={"rejected": [RejectedClaim(kind="demand", quote="", reason="not_in_source")]}
    )
    assert _decide(v).conclusion == Conclusion.INPUT_REQUIRED
```

`ai/tests/fixtures/cases/voice/V2.yaml` (새 파일. `test_case_voice.py`의 `test_case_fixture`가 자동으로 읽는다)

```yaml
id: V2
scenario: voice_phishing
# 우체국 배송기사 사칭 + 카드사 번호로 전화 유도. 실제 whisper 대본처럼 받는 사람 말이 섞여 있고,
# 상대는 "-시라고요" 로 자기 지시를 되풀이한다 (ADR 0008, ADR 0010)
input: {kind: audio}
transcript:
  - "네 여보세요 안녕하세요 우체국 배송기사입니다"
  - "네 네 기사님 맞으시죠?"
  - "제가 카드사 대표 번호 불러줄게요 지금 메모가 될까요?"
  - "왜요?"
  - "불러주면 어떻게 하라고?"
  - "아 불러줘서 전화하시라고요."
  - "왜 전화를 해요?"
runtime:
  search: kb_empty
  reading:
    identity: [{kind: institution, name: 우체국, quote: "우체국 배송기사입니다"}]
    demands: [{kind: call_number, quote: "불러줘서 전화하시라고요"}]
expect:
  conclusion: inconclusive
  caution: true
  floors: []
  stop_actions: [dont_call_number_in_message, hang_up]
  questions: {already_acted: [entered_info, installed_app, sent_money, not_yet]}
  rejected: []
```

## 결과

| 코드 | 이 녹음의 결론 | 카드 | 근거 |
| --- | --- | --- | --- |
| `732b994` 이전 | `input_required` | 🎙️ 녹음에 사칭·요구하는 말이 없어요 (VE4) | 없음 (신원·요구 모두 `self_side`) |
| 현재, ②③ 전 | `inconclusive` + `caution` | 🔎 주의가 필요한 통화예요 | 신원만 (요구는 `self_side`) |
| 현재 + ②③ | `inconclusive` + `caution` | 🔎 주의가 필요한 통화예요 | 신원 + 전화 유도(`call_request`), `rejected` 없음 |

②③과 새 테스트를 넣은 상태에서 AI 테스트 1,816개와 BE 테스트 757개가 모두 통과했다(각 1 skipped).

## 이번 범위 밖

- "의심"까지는 올라가지 않는다. ADR 0010상 우체국은 주의까지만이고, `call_number`는 L-3 짝(`_L3_DEMANDS`)에 없다. 카드 배송 사칭을 의심으로 올릴지는 따로 정한다.
- 질문 꼴 요구("영동포인가요?")와 질문으로 끝나는 문장 속 명분("메모가 될까요?")은 여전히 `is_self_talk()`에 걸려 빠진다.
- LLM이 "우체국 배송기사"를 `company`(택배사)로 읽으면 신원 근거가 생기지 않는다. `verify_call()`은 `company`를 근거로 쓰지 않는다.
- `116f7dc`가 옮긴 말("검찰입니다 라고 하더라고요")을 잘라 상대의 자기소개처럼 만드는 문제 (① 참고)
- ②는 받는 사람이 "송금하시라고요?"처럼 높임째 따라 하면 상대 말로 잡힐 수 있다. 드문 꼴이라 감수한다.
