import { describe, expect, it } from "vitest";
import { markMessage } from "./markMessage";
import type { Highlight } from "../contract";

function quote(text: string, kind: Highlight["kind"] = "claim"): Highlight {
  return { quote: text, kind, label: `${text} 라벨`, note: `${text} 설명` };
}

const text = "[청소행정과] 쓰레기 무단투기 신고가 접수되었습니다. 아래 링크에서 본인 정보를 입력";

describe("markMessage: 무해화한 원문에 근거 인용을 표시한다", () => {
  it("인용이 없으면 무해화한 원문 한 조각이고 설명은 없다", () => {
    expect(markMessage("링크 https://a.example/b 확인", [])).toEqual({
      pieces: [{ text: "링크 hxxps://a[.]example/b 확인" }],
      notes: [],
    });
  });

  it("원문에 있는 인용은 칠한 조각이 되고 같은 번호의 설명이 붙는다", () => {
    const claim = quote("청소행정과");
    expect(markMessage(text, [claim])).toEqual({
      pieces: [
        { text: "[" },
        { text: "청소행정과", number: 1, kind: "claim" },
        { text: "] 쓰레기 무단투기 신고가 접수되었습니다. 아래 링크에서 본인 정보를 입력" },
      ],
      notes: [{ number: 1, highlight: claim }],
    });
  });

  it("원문에 없는 인용은 칠하지도 설명하지도 않는다", () => {
    expect(markMessage(text, [quote("원문에 없는 문구")])).toEqual({
      pieces: [{ text }],
      notes: [],
    });
  });

  it("인용 순서와 원문 순서가 달라도 조각은 원문 순서, 번호와 설명은 인용 순서다", () => {
    const request = quote("본인 정보를 입력", "request");
    const claim = quote("청소행정과");
    const { pieces, notes } = markMessage(text, [request, claim]);
    expect(pieces.filter((piece) => "number" in piece)).toEqual([
      { text: "청소행정과", number: 2, kind: "claim" },
      { text: "본인 정보를 입력", number: 1, kind: "request" },
    ]);
    expect(notes).toEqual([
      { number: 1, highlight: request },
      { number: 2, highlight: claim },
    ]);
  });

  it("겹치는 인용은 먼저 시작하는 것만 칠하고, 칠하지 못한 인용은 설명도 뺀다", () => {
    const first = quote("쓰레기 무단투기");
    const second = quote("무단투기 신고가");
    const { pieces, notes } = markMessage(text, [first, second]);
    expect(pieces.filter((piece) => "number" in piece)).toEqual([
      { text: "쓰레기 무단투기", number: 1, kind: "claim" },
    ]);
    expect(notes).toEqual([{ number: 1, highlight: first }]);
  });

  it("같은 인용이 두 번 나오면 첫 자리만 칠한다", () => {
    expect(markMessage("링크 링크", [quote("링크")]).pieces).toEqual([
      { text: "링크", number: 1, kind: "claim" },
      { text: " 링크" },
    ]);
  });

  it("빈 인용은 무시한다", () => {
    expect(markMessage(text, [quote("")])).toEqual({ pieces: [{ text }], notes: [] });
  });

  it("주소가 든 인용도 무해화한 원문에서 찾아 칠하고, 칠한 글자도 무해화돼 있다", () => {
    const signal = quote("https://cj-check.example/a", "signal");
    expect(markMessage("아래 링크 https://cj-check.example/a 에서 확인", [signal]).pieces).toEqual([
      { text: "아래 링크 " },
      { text: "hxxps://cj-check[.]example/a", number: 1, kind: "signal" },
      { text: " 에서 확인" },
    ]);
  });
});
