import re

def split_message(text: str) -> tuple[list[str], str]:
    """
    문자 원문에서 링크와 순수 메시지 본문을 분리한다.
    
    links: 발견된 URL 전체 (0개/1개/2개 이상 모두 가능 — B03의 개수 검증에 그대로 사용)
    message: 링크를 제거하고 남은 텍스트 (AI의 문자 해석 단계 입력으로 사용)"""
    pattern = r'https?://[^\s]+'
    links = re.findall(pattern, text)
    message = re.sub(pattern, '', text)
    message = re.sub(r'\s+', ' ', message).strip()  # 링크 빠진 자리 공백 정리
    return links, message
