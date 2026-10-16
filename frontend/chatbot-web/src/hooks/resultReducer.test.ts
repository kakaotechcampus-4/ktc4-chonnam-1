import { describe, expect, it } from "vitest";
import { resultReducer, type ResultState } from "./useResult";
import { parseResultView } from "../api/parseResultView";
import caution from "../../public/mock/caution.json";

const view = parseResultView(caution);
const loading: ResultState = { status: "loading" };

describe("resultReducer: 결과 불러오기 상태", () => {
  it("SUCCESS면 결과를 담은 준비됨 상태", () => {
    expect(resultReducer(loading, { type: "SUCCESS", view })).toEqual({ status: "ready", view });
  });

  it("FAIL이면 오류 종류를 담은 오류 상태", () => {
    expect(resultReducer(loading, { type: "FAIL", error: "expired" })).toEqual({
      status: "error",
      error: "expired",
    });
  });

  it("오류 뒤 LOAD면 다시 불러오는 중 (입장권이 바뀐 경우)", () => {
    const error: ResultState = { status: "error", error: "failed" };
    expect(resultReducer(error, { type: "LOAD" })).toEqual({ status: "loading" });
  });
});
