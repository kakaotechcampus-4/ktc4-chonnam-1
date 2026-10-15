import { describe, expect, it } from "vitest";
import { findSpans } from "./spans";

describe("findSpans: 원문에서 근거 인용 위치를 찾는다", () => {
  const text = "[청소행정과] 쓰레기 무단투기 신고가 접수되었습니다. 아래 링크에서 본인 정보를 입력";

  it("인용이 있는 자리를 순서대로 돌려준다", () => {
    expect(findSpans(text, ["청소행정과", "본인 정보를 입력"])).toEqual([
      { start: 1, end: 6, index: 0 },
      { start: 38, end: 47, index: 1 },
    ]);
  });

  it("원문에 없는 인용은 칠하지 않는다", () => {
    expect(findSpans(text, ["원문에 없는 문구"])).toEqual([]);
  });

  it("빈 인용은 무시한다", () => {
    expect(findSpans(text, [""])).toEqual([]);
  });

  it("겹치는 인용은 먼저 시작하는 것만 남긴다", () => {
    const spans = findSpans(text, ["쓰레기 무단투기", "무단투기 신고가"]);
    expect(spans).toEqual([{ start: 8, end: 16, index: 0 }]);
  });

  it("인용 순서와 원문 순서가 달라도 원문 순서로 정렬하고 원래 번호를 지킨다", () => {
    const spans = findSpans(text, ["본인 정보를 입력", "청소행정과"]);
    expect(spans.map((s) => s.index)).toEqual([1, 0]);
  });

  it("같은 인용이 두 번 나오면 첫 자리만 칠한다", () => {
    expect(findSpans("링크 링크", ["링크"])).toEqual([{ start: 0, end: 2, index: 0 }]);
  });
});
