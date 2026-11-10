"""BE 쪽 시간 상한. 근거와 전체 표는 docs/latency-budget.md.

값을 바꾸면 그 문서도 함께 고친다.
"""

# 첫 응답 뒤 백그라운드 분석 전체. 넘으면 작업을 취소하고 시간 초과 결과를 저장한다
ANALYSIS_DEADLINE_SECONDS = 45.0

# analyze_call()·analyze_message_case() 에 줄 예산의 출발값. 전체 상한에서 카드 렌더링·저장 여유
# 5초를 뺀다. 상한을 그대로 넘기면 분석이 45초를 다 써서 카드를 만들기 전에 작업이 취소된다.
# 호출부는 여기서 이미 쓴 시간(다운로드·STT·read_message)을 다시 뺀다
CASE_ANALYZE_BUDGET_S = ANALYSIS_DEADLINE_SECONDS - 5.0
