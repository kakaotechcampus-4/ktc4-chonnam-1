# infra

`isolation/`은 격리 EC2의 Docker·SSH·방화벽 설정과 검증 스크립트입니다.
접속 방법과 적용 결과는 [AWS 격리환경 작업 기록](../docs/experiments/2026-10-07-aws-isolation-setup.md)을 참고하세요.

화이트리스트가 YAML이고 캐시가 메모리인 동안은 DB가 불필요합니다.
필요해지면 `migrations/` `seeds/` 를 여기에 추가하세요.
