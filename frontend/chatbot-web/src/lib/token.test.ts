import { describe, expect, it } from "vitest";
import { isDemo, parseToken } from "./token";

describe("parseToken: 주소 # 뒤의 입장권을 읽는다", () => {
  it("#t= 값을 돌려준다", () => {
    expect(parseToken("#t=x7Kq")).toBe("x7Kq");
  });

  it("다른 값과 섞여 있어도 t만 읽는다", () => {
    expect(parseToken("#a=1&t=x7Kq")).toBe("x7Kq");
  });

  it("앞뒤 공백은 지운다", () => {
    expect(parseToken("#t=%20x7Kq%20")).toBe("x7Kq");
  });

  it("# 뒤가 비었거나 t가 없으면 null", () => {
    expect(parseToken("")).toBeNull();
    expect(parseToken("#")).toBeNull();
    expect(parseToken("#a=1")).toBeNull();
    expect(parseToken("#t=")).toBeNull();
  });
});

describe("isDemo: 가짜 데이터 입장권인지", () => {
  it("demo-로 시작하면 가짜 데이터", () => {
    expect(isDemo("demo-caution")).toBe(true);
    expect(isDemo("x7Kq")).toBe(false);
  });
});
