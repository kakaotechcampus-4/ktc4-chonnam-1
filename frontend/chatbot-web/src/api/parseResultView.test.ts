import { describe, expect, it } from "vitest";
import { parseResultView } from "./parseResultView";
import caution from "../../public/mock/caution.json";

describe("parseResultView: 서버 응답에서 필수 필드를 확인한다", () => {
  it("올바른 응답은 그대로 돌려준다", () => {
    expect(parseResultView(caution)).toEqual(caution);
  });

  it("객체가 아니면 거절한다", () => {
    expect(() => parseResultView(null)).toThrow();
    expect(() => parseResultView("문자열")).toThrow();
  });

  it("알 수 없는 tone은 거절한다", () => {
    expect(() => parseResultView({ ...caution, tone: "safe" })).toThrow();
  });

  it("문자열이어야 하는 필드가 빠지면 거절한다", () => {
    const withoutHeadline: Record<string, unknown> = { ...caution };
    delete withoutHeadline.headline;
    expect(() => parseResultView(withoutHeadline)).toThrow();
  });

  it("문자열 배열 필드에 다른 값이 섞이면 거절한다", () => {
    expect(() => parseResultView({ ...caution, checked: ["확인", 1] })).toThrow();
  });

  it("알 수 없는 강조 종류는 거절한다", () => {
    const highlights = [{ ...caution.highlights[0], kind: "verdict" }];
    expect(() => parseResultView({ ...caution, highlights })).toThrow();
  });

  it("공식 확인 방법의 year는 문자열이나 null만 받는다", () => {
    const guidance = [{ ...caution.guidance[0], year: null }];
    expect(parseResultView({ ...caution, guidance }).guidance[0].year).toBeNull();
    expect(() =>
      parseResultView({ ...caution, guidance: [{ ...caution.guidance[0], year: 2025 }] }),
    ).toThrow();
  });
});
