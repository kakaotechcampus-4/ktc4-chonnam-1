"""공식 브랜드 ↔ 도메인 화이트리스트.

판정의 근거이므로 여기 없는 브랜드는 "official/brand_mismatch"를
추측하지 않고 `not_registered`로 처리한다 (BE_DOMAIN_MATCH_TASK_GUIDE.md).
확인되지 않은 도메인을 넣느니 비워두는 쪽이 안전하다.

브랜드 키는 `ai/src/ai/types.py`의 `Brand` enum 값(한글 문자열)과 맞춘다.
"""

# 실제 확인된 공식 도메인만 등재한다. 여러 개면 전부 공식 도메인으로 인정한다
# (서브도메인은 official_domain_service.is_same_or_subdomain()이 별도로 처리).
OFFICIAL_DOMAINS: dict[str, set[str]] = {
    # CJ대한통운/CJ택배/CJ익스프레스는 실사용 문자에서 표기가 갈리지만
    # 전부 같은 CJ대한통운 배송 서비스를 가리킨다 (docs/ai/Revisions.md
    # 브랜드 후보 근거 참고). 별도 도메인이 확인되기 전까지 하나로 묶는다.
    "CJ대한통운": {"cjlogistics.com"},
    "CJ택배": {"cjlogistics.com"},
    "CJ익스프레스": {"cjlogistics.com"},
    # 쇼핑몰 자체 사이트 (배송사가 아니라 발신 주체로서의 공식 도메인).
    "CJ오쇼핑": {"cjonstyle.com"},
    # 한진택배는 기업 사이트(hanjin.com)와 소비자 조회 사이트(hanjin.co.kr)가
    # 공존한다. 둘 다 실제 한진 소유로 확인됨.
    "한진택배": {"hanjin.com", "hanjin.co.kr"},
    "로젠택배": {"ilogen.com"},
    # 정부기관 도메인(.go.kr). 우체국 택배 조회는 이 아래 서브도메인에서 이뤄진다.
    "우체국택배": {"epost.go.kr"},
    # 2016년 현대로지스틱스가 인수되며 '현대택배'는 폐업하고 이 이름으로
    # 바뀌었다. 롯데택배 문자 판정에 사용한다.
    "롯데택배": {"lotteglogis.com"},
    "경동택배": {"kdexp.com"},
    "합동택배": {"hdexp.co.kr"},
    "DHL": {"dhl.com"},
    # 테스트/예시용 — BE_DOMAIN_MATCH_TASK_GUIDE.md의 필수 테스트 케이스가
    # 이 브랜드를 기준으로 작성되어 있어 그대로 유지한다.
    "NAVER": {"naver.com"},
}

# 확인이 안 돼서 아직 등재하지 않은 브랜드. 실제 도메인이 확정되면
# 위 OFFICIAL_DOMAINS로 옮긴다 — 추측으로 채우지 않는다.
#
# - 대신택배: daesin.co.kr / ds3211.co.kr / daesinparcel.com 등 상충되는
#   정보만 확인됨. 어느 게 실제 1차 공식 도메인인지 별도 확인 필요.
# - KGB택배: 2017년 KG로지스로 개편된 것으로 보이며, kgb.co.kr은 무관한
#   포장이사 업체 도메인으로 확인됨. 현재 공식 도메인 미확인.
# - 현대택배: 2016년 폐업, 롯데글로벌로지스(롯데택배)로 흡수됨. 지금
#   '현대택배'를 자처하는 링크는 그 자체로 의심 신호에 가깝다 — 여기서
#   임의로 롯데택배 도메인과 연결하지 않는다 (브랜드명 자체의 진위는
#   이 서비스의 판정 범위 밖).
# - 쿠팡·옥션·롯데몰·카카오톡 선물하기·7-11·라쿠텐 익스프레스·KISA·검찰청:
#   AI Brand enum에는 있으나 택배 사칭이 아닌 다른 시나리오용이라 이번
#   1차 작업 범위에서 조사하지 않음.
