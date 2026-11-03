"""문자 사례 첨부 관문 학습 전용 합성 정상 알림을 만든다.

ai/ 에서 실행한다. 결과는 datasets/text_gate_train.jsonl(`split: train`)이고 평가·KB 에 쓰지 않는다.

    python eval/text_gate_train_build.py

공식 알림에서 흔한 형식(택배·결제 승인·은행·공공·과태료·검진·경조사·쿠폰·통신·계정)을 틀로 두고 기관명·
금액·날짜를 바꿔 채운다. 평가셋(rag_testset.jsonl)과 문자 n-gram 유사도가 MAX_EVAL_SIMILARITY 이상인 행은
뺀다. 평가 문장을 베낀 학습 자료로 기준을 맞추면 평가 점수만 오르기 때문이다. 같은 입력이면 같은 결과다.
"""

import hashlib
import itertools
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
OUTPUT = EVAL_DIR / "datasets" / "text_gate_train.jsonl"
TESTSET = EVAL_DIR / "datasets" / "rag_testset.jsonl"
SEED = "text-gate-train-2026-10-31"
PER_FRAME = 4
MAX_EVAL_SIMILARITY = 0.6

COURIERS = ["CJ대한통운", "한진택배", "롯데택배", "로젠택배", "우체국택배", "경동택배", "쿠팡로지스틱스"]
SHOPS = ["쿠팡", "11번가", "G마켓", "SSG.COM", "무신사", "오늘의집", "마켓컬리"]
CARDS = ["신한카드", "KB국민카드", "삼성카드", "현대카드", "롯데카드", "우리카드", "하나카드", "NH농협카드"]
BANKS = ["KB국민은행", "신한은행", "우리은행", "하나은행", "NH농협은행", "IBK기업은행", "카카오뱅크", "토스뱅크"]
STORES = ["GS25", "CU", "스타벅스", "이마트", "올리브영", "배달의민족", "다이소"]
TELCOS = ["SKT", "KT", "LG U+"]
AMOUNTS = ["12,800원", "34,500원", "58,000원", "129,000원", "6,900원", "247,300원"]
DATES = ["11/03", "11/12", "12/01", "12/15", "01/08", "02/20"]
TIMES = ["09:14", "13:42", "18:05", "21:30", "07:58"]
NAMES = ["<이름>"]

FRAMES: dict[str, list[str]] = {
    "delivery": [
        "[{courier}] {name}님의 상품이 배송 완료되었습니다. 문 앞에 두었습니다.",
        "[{courier}] 오늘 {time}~{time2} 사이 배송 예정입니다. 부재 시 경비실에 맡깁니다.",
        "[{shop}] 주문하신 상품이 출고되었습니다. 배송은 {courier}에서 진행합니다.",
        "[{courier}] 고객님의 택배가 집화 처리되었습니다. 운송장번호는 앱에서 확인 가능합니다.",
        "[{shop}] 로켓배송 상품이 {date} 도착 예정입니다. 감사합니다.",
        "[{courier}] 배송기사입니다. 오후에 방문 예정이며 부재 시 문 앞에 두겠습니다.",
        "[{courier}] 반품 회수가 완료되었습니다. 환불은 판매자 확인 후 진행됩니다.",
    ],
    "address": [
        "[{shop}] 배송지 변경이 완료되었습니다. 변경된 주소로 배송됩니다.",
        "[{shop}] {name}님 주문의 배송지가 기본 배송지로 등록되었습니다.",
        "[카카오톡 선물하기] {name}님이 선물을 받으셨어요. 배송 준비 중입니다.",
        "[{courier}] 요청하신 배송 희망일({date})로 일정이 조정되었습니다.",
    ],
    "payment": [
        "[{card}] {name}님 {date} {time} {amount} 일시불 승인 {store}",
        "[{card}] {date} {store} {amount} 결제 취소 완료",
        "[{card}] {name}님 {date} 해외 {amount} 승인. 본인 결제가 아니면 카드 뒷면 고객센터로 연락 바랍니다.",
        "[{shop}] 결제가 완료되었습니다. 결제금액 {amount}, 주문내역은 마이페이지에서 확인하세요.",
        "[{card}] {month}월 결제예정금액은 {amount}입니다. 결제일 {date}",
        "[{store}] {amount} 결제되었습니다. 이용해 주셔서 감사합니다.",
    ],
    "bank": [
        "[{bank}] {date} {time} 입금 {amount} 잔액은 앱에서 확인하세요.",
        "[{bank}] 고객님의 계좌에서 {amount} 자동이체 출금되었습니다.",
        "[{bank}] 새로운 기기에서 로그인되었습니다. 본인이 아니면 영업점이나 고객센터로 문의하세요.",
        "[{bank}] 인증서가 {date} 만료 예정입니다. 앱에서 갱신해 주세요.",
        "[{bank}] 예금 만기일이 {date}입니다. 만기 후 처리 방법은 앱이나 영업점에서 선택하실 수 있습니다.",
        "[{card}] 신규 카드가 발급되어 {courier}로 발송되었습니다. 수령 후 앱에서 등록해 주세요.",
    ],
    "public": [
        "[국민건강보험공단] {name}님은 올해 건강검진 대상자입니다. 가까운 검진기관에 예약 후 받으세요.",
        "[국세청] 종합소득세 신고기간은 5월 31일까지입니다. 홈택스에서 신고할 수 있습니다.",
        "[행정안전부] 신청하신 지원금이 {card}로 지급되었습니다. 사용기한은 {date}까지입니다.",
        "[{city}] 재산세 납부기간입니다. 위택스나 은행 앱에서 납부하실 수 있습니다.",
        "[{city}] 주민등록증 재발급이 완료되었습니다. 신분증 지참 후 주민센터에서 수령하세요.",
        "[국민연금공단] {month}월 연금 {amount}이 지급되었습니다.",
        "[{city}] {date} 정기 소독이 진행됩니다. 주민 여러분의 협조 바랍니다.",
    ],
    "penalty": [
        "[경찰청 교통민원24] 과태료 납부가 완료되었습니다. 감사합니다.",
        "[{city}] 주정차 위반 과태료 사전통지서가 등기로 발송되었습니다. 의견 진술은 기한 내 구청에 하실 수 있습니다.",
        "[{city}] 자동차세 {amount} 납부가 확인되었습니다.",
        "[한국도로공사] 통행료 미납 {amount}이 있습니다. 고속도로 영업소나 공식 앱에서 납부하세요.",
    ],
    "health": [
        "[{hospital}] {name}님 {date} {time} 진료 예약이 확정되었습니다.",
        "[{hospital}] 검진 결과가 나왔습니다. 결과지는 내원하시거나 우편으로 받으실 수 있습니다.",
        "[{pharmacy}] 처방전 조제가 완료되었습니다. 방문 수령 바랍니다.",
        "[{hospital}] 내일 {time} 예약 잊지 마세요. 변경은 대표번호로 연락 주세요.",
    ],
    "family": [
        "{name} 결혼식 {date} 토요일 오후 1시예요. 시간 되면 와줘!",
        "부고 알려드립니다. 저희 부친께서 별세하셨습니다. 빈소는 서울대병원 장례식장입니다.",
        "엄마 나 오늘 야근이라 늦어. 저녁 먼저 먹어",
        "아빠 차 정기점검 내일 맡기기로 했어요. 끝나면 연락할게요",
        "우리 아이 첫돌 잔치 {date} 점심에 해요. 와주시면 감사하겠습니다.",
        "누나 택배 왔길래 현관 앞에 들여놨어",
        "할머니 생신 모임 이번 주 일요일 12시 집에서 해요",
    ],
    "event": [
        "[{store}] {name}님 생일 축하 쿠폰이 발급되었습니다. 앱 쿠폰함에서 확인하세요.",
        "[{store}] 적립 포인트 1,200P가 소멸 예정입니다({date}).",
        "[{shop}] 참여하신 이벤트 당첨자 발표는 {date} 공지사항에서 확인하실 수 있습니다.",
        "[{store}] 모바일 상품권이 선물함에 도착했어요. 유효기간 {date}",
    ],
    "telecom": [
        "[{telco}] {month}월 이용요금은 {amount}이며 {date}에 자동이체됩니다.",
        "[{telco}] 데이터 사용량이 기본 제공량의 80%에 도달했습니다.",
        "[{telco}] 요금제 변경이 완료되었습니다. 다음 달 1일부터 적용됩니다.",
        "[{telco}] 해외로밍이 시작되었습니다. 현지에서 안전한 여행 되세요.",
    ],
    "account": [
        "[카카오] 카카오계정 비밀번호가 변경되었습니다. 본인이 아니라면 고객센터에 문의하세요.",
        "[네이버] 새로운 환경에서 로그인되었습니다. 본인이 맞다면 이 알림은 무시하셔도 됩니다.",
        "[{shop}] 회원정보가 수정되었습니다.",
        "인증번호 [******]를 입력해 주세요. 타인에게 알려주지 마세요.",
    ],
}
FILLS = {
    "courier": COURIERS, "shop": SHOPS, "card": CARDS, "bank": BANKS, "store": STORES, "telco": TELCOS,
    "amount": AMOUNTS, "date": DATES, "time": TIMES, "time2": ["15:00", "18:00", "20:00"], "name": NAMES,
    "month": ["10", "11", "12"], "city": ["서울특별시 마포구", "부산광역시", "수원시", "대전광역시 서구", "성남시"],
    "hospital": ["서울아산병원", "연세세브란스병원", "강남차병원", "OO내과의원"], "pharmacy": ["온누리약국", "OO약국"],
}


def _order(key: str) -> str:
    return hashlib.sha256(f"{SEED}:{key}".encode()).hexdigest()


def fill(frame: str, index: int) -> str:
    values = {key: choices[int(_order(f"{frame}:{index}:{key}")[:8], 16) % len(choices)] for key, choices in FILLS.items()}
    return frame.format(**values)


def build() -> list[dict]:
    sys.path.insert(0, str(EVAL_DIR.parent / "src"))
    from ai.kb.normalize import normalize
    from ai.kb.search import Case, build_index, rank

    eval_rows = [json.loads(line) for line in TESTSET.read_text(encoding="utf-8").splitlines() if line.strip()]
    eval_index = build_index(
        Case(row["id"], (row["message"],), (normalize(row["message"]),), ()) for row in eval_rows
    )
    rows, seen = [], set()
    for kind, frames in FRAMES.items():
        for f, frame in enumerate(frames):
            for i in itertools.islice(itertools.count(), PER_FRAME * 3):
                text = fill(frame, i)
                if text in seen:
                    continue
                if rank(eval_index, text)[0].similarity >= MAX_EVAL_SIMILARITY:
                    continue
                seen.add(text)
                rows.append({"id": f"G-{kind}-{f:02d}-{len([r for r in rows if r['kind'] == kind])}", "kind": kind,
                             "label": "benign", "text": text, "origin": "synthetic", "split": "train"})
                if sum(r["text"].startswith(frame[:6]) for r in rows if r["kind"] == kind) >= PER_FRAME:
                    break
    return rows


def main() -> int:
    rows = build()
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"{OUTPUT.name}: {len(rows)}행", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
