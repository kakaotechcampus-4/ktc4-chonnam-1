# 문서 설명

이 문서는 BE와의 통신에 사용할 스키마에 대한 정의를 메인으로 담고 있다.

## BE -> AI : urlscan(임시) API 결과 반환
스미싱 메시지에 포함된 링크만을 분석한 결과이다. 

현재는 외부 API인 `urlscan을 이용한 결과값을 도출하고, 추후 고유 시스템 개발을 통해 urlscan API의 반환값 중 AI 파트로 넘기는 요소들과 동일한 요소 추출을 목표로 한다. 

```json
{
    final_url : "https://xxxx.xxx.xxx/xxx/xxxx",
    domain : "https://xxxx.xxx.xxx",
    official : true/false
}
```

- `final_url` : 최종 목적지 url
- `domain` : 도메인 주소
- `official` : 공식 도메인 여부(`urlscan` API 기준으로는 score가 일정 점수 이상일 때를 기준으로 `BE` 측에서 `true/false`로 2차 가공 후 전달 예정)

## AI -> BE : 최종 분석 결과 반환
다음 3개의 요소를 종합적으로 분석한 결과이다.
- BE -> AI의 메시지 링크 분석(현재는 urlscan 분석 사용 중)
- 메시지 내용 분석(BE 측에서 원본 메시지 중 url만을 제거한 내용)
- 격리 환경 분석 결과(격리 환경 측에서 구조화 후 반환 예정)

격리 환경 분석 결과 스키마는 다음과 같다.
```json
{
    brand : "xx택배",
    category : "택배",
    info : "택배 배송 조회, etc...",
}
```

- `brand` : 사이트 관련 회사 (ex. 한진 택배, 로젠 택배, KB국민은행 등등)
- `category` : 사이트 관련 

{
    url : {
        final_url : "",
        domain : "",
        official : false
    },
    message : {
        brand : "",
        category : "",
        reason : "",
        answer : true,
        details : {
            doubt : "",
            reason : ""
        }
    },
    env : {
        brand : "",
        category : "",
        info : "",
        answer : true,
        details : {
            doubt : "",
            reason : ""
        }
    }, 
    result : true
}

url : 링크 분석 결과
- final_url : 최종 목적지 url (ex. https://xxx.xxx.xxx/xxx/xxx)
- domain : 도메인 (ex. https://xxx.xxx.xxx/)
- official : 공식 도메인 여부(true/false)

message : 메시지 내용 분석 결과
- brand : 메시지 내용 관련 회사 (ex. 한진택배, 로젠택배, KB국민은행 등등)
- category : 메시지 내용 관련 종류 (ex. 택배, 은행, 배달 등등)
- answer : 신뢰도(true - 안전 / false - 의심)
- details : 신뢰도 관련 내용
- doubt : 신뢰도 세부 판단 (ex. 앱 설치, 결제 유도 등등)
- reason : 신뢰도 판단 근거

env : 격리 환경 분석 결과
- brand : 실제 페이지 속 관련 회사
- category : 실제 페이지 속 관련 종류
- answer : 신뢰도(true - 안전 / false - 의심)
- details : 신뢰도 관련 내용
- doubt : 신뢰도 세부 판단
- reason : 신뢰도 판단 근거

result : 최종 판단 결과(true - 안전 / false - 의심)