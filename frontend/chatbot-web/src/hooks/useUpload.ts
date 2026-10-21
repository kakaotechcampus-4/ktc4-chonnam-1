import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type { FileProblem, UploadError } from "../contract";
import { checkAudioFile } from "../lib/audioFile";
import { serverFor } from "../api/server";

/** 상태를 묻는 간격. */
export const POLL_INTERVAL_MS = 2_000;

/**
 * 업로드 화면 상태. 결과는 웹에 보여 주지 않으므로 분석이 끝나면 챗봇으로 돌려보내기만 한다.
 * - choosing: 파일 고르기. fileProblem은 파일 자체의 문제(보내기 막음),
 *   sendFailed는 인터넷 끊김·서버 오류로 못 보냄(같은 파일로 다시 보낼 수 있음)
 * - slow: 3분이 지났거나 기다리는 중 입장권이 만료됨. 분석은 서버에서 계속된다
 * - expired: 입장권 없음·만료, used: 이 입장권으로 이미 올림
 */
export type UploadState =
  | { phase: "choosing"; file: File | null; fileProblem: FileProblem | null; sendFailed: boolean }
  | { phase: "sending"; file: File }
  | { phase: "analyzing"; caseId: string }
  | { phase: "finished" }
  | { phase: "slow" }
  | { phase: "analysis_failed" }
  | { phase: "expired" }
  | { phase: "used" };

export type UploadAction =
  | { type: "CHOOSE"; file: File }
  | { type: "SEND" }
  | { type: "ACCEPTED"; caseId: string }
  | { type: "REJECTED"; error: UploadError }
  | { type: "FINISHED" }
  | { type: "ANALYSIS_FAILED" }
  | { type: "SLOW" };

type Choosing = Extract<UploadState, { phase: "choosing" }>;

/** 보낼 수 있나: 고르기 화면이고, 파일이 있고, 파일에 문제가 없다. 보내기 실패는 막지 않는다. */
export function canSend(state: UploadState): state is Choosing & { file: File } {
  return state.phase === "choosing" && state.file !== null && state.fileProblem === null;
}

export function uploadReducer(state: UploadState, action: UploadAction): UploadState {
  switch (action.type) {
    case "CHOOSE":
      if (state.phase !== "choosing") return state;
      return {
        phase: "choosing",
        file: action.file,
        fileProblem: checkAudioFile(action.file),
        sendFailed: false,
      };
    case "SEND":
      if (!canSend(state)) return state;
      return { phase: "sending", file: state.file };
    case "ACCEPTED":
      return { phase: "analyzing", caseId: action.caseId };
    case "REJECTED":
      if (action.error === "expired" || action.error === "used") return { phase: action.error };
      // 보내기 실패와 서버가 거절한 파일은 고른 파일을 그대로 두고 고르기 화면으로 돌아간다.
      return {
        phase: "choosing",
        file: state.phase === "sending" ? state.file : null,
        fileProblem: action.error === "send_failed" ? null : action.error,
        sendFailed: action.error === "send_failed",
      };
    case "FINISHED":
      return { phase: "finished" };
    case "ANALYSIS_FAILED":
      return { phase: "analysis_failed" };
    case "SLOW":
      return { phase: "slow" };
  }
}

function initialState(token: string | null): UploadState {
  return token
    ? { phase: "choosing", file: null, fileProblem: null, sendFailed: false }
    : { phase: "expired" };
}

/** 녹음 고르기 → 보내기 → 분석이 끝날 때까지 상태 묻기. */
export function useUpload(token: string | null) {
  const [state, dispatch] = useReducer(uploadReducer, token, initialState);
  const server = useMemo(() => (token ? serverFor(token) : null), [token]);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const choose = useCallback((file: File) => dispatch({ type: "CHOOSE", file }), []);

  const send = useCallback(() => {
    if (!server || !canSend(state)) return;
    dispatch({ type: "SEND" });
    server.uploadRecording(state.file).then((result) => {
      if (!mounted.current) return;
      dispatch(
        result.ok
          ? { type: "ACCEPTED", caseId: result.data }
          : { type: "REJECTED", error: result.error },
      );
    });
  }, [server, state]);

  const caseId = state.phase === "analyzing" ? state.caseId : null;

  useEffect(() => {
    if (!server || !caseId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    // 기다리는 시간은 대답과 상관없이 따로 잰다. 대답이 오지 않아도 화면이 멈추지 않게 한다.
    const deadline = setTimeout(() => {
      stopped = true;
      clearTimeout(timer);
      dispatch({ type: "SLOW" });
    }, server.pollLimitMs);

    const schedule = () => {
      timer = setTimeout(ask, POLL_INTERVAL_MS);
    };
    const ask = () => {
      server.fetchCaseStatus(caseId).then((result) => {
        if (stopped) return;
        if (!result.ok) {
          // 기다리는 중 입장권이 만료돼도 분석은 서버에서 계속된다. 챗봇에서 결과를 확인하게 한다.
          if (result.error === "expired") dispatch({ type: "SLOW" });
          else schedule();
        } else if (result.data === "running") schedule();
        else if (result.data === "failed") dispatch({ type: "ANALYSIS_FAILED" });
        // 질문 대기(waiting_answer)와 케이스 만료도 웹에서는 끝이다. 질문과 결과는 챗봇 카드가 보여 준다.
        else dispatch({ type: "FINISHED" });
      });
    };

    timer = setTimeout(ask, POLL_INTERVAL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(deadline);
    };
  }, [server, caseId]);

  return { state, canSend: canSend(state), choose, send };
}
