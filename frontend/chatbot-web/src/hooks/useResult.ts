import { useEffect, useReducer } from "react";
import type { LoadError, ResultView } from "../types";
import { fetchResult } from "../api/result";

export type ResultState =
  | { status: "loading" }
  | { status: "ready"; view: ResultView }
  | { status: "error"; error: LoadError };

export type ResultAction =
  { type: "LOAD" } | { type: "SUCCESS"; view: ResultView } | { type: "FAIL"; error: LoadError };

/** 불러오는 중 → 준비됨 / 오류. 상태마다 가질 수 있는 값이 정해져 있다. */
export function resultReducer(_state: ResultState, action: ResultAction): ResultState {
  switch (action.type) {
    case "LOAD":
      return { status: "loading" };
    case "SUCCESS":
      return { status: "ready", view: action.view };
    case "FAIL":
      return { status: "error", error: action.error };
  }
}

function initialState(token: string | null): ResultState {
  return token ? { status: "loading" } : { status: "error", error: "no_token" };
}

function toLoadError(error: unknown): LoadError {
  return error === "expired" ? "expired" : "failed";
}

/** 입장권으로 결과를 받는다. 입장권이 바뀌면 다시 받고, 늦게 온 이전 응답은 버린다. */
export function useResult(token: string | null): ResultState {
  const [state, dispatch] = useReducer(resultReducer, token, initialState);

  useEffect(() => {
    if (!token) {
      dispatch({ type: "FAIL", error: "no_token" });
      return;
    }
    let ignore = false;
    dispatch({ type: "LOAD" });
    fetchResult(token)
      .then((view) => {
        if (!ignore) dispatch({ type: "SUCCESS", view });
      })
      .catch((error: unknown) => {
        if (!ignore) dispatch({ type: "FAIL", error: toLoadError(error) });
      });
    return () => {
      ignore = true;
    };
  }, [token]);

  return state;
}
