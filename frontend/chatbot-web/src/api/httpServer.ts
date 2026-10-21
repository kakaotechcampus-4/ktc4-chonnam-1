import { CaseCreated, CaseStatusResponse, FileRejection, ResultView } from "../contract";
import { callServer } from "./callServer";
import type { Server } from "./server";

const RESULT_API = "/api/result";
const CASES_API = "/api/cases";

/** 이만큼 지나도 안 끝나면 묻기를 멈춘다. 웹 경로 분석 예산 90초(§9 B4)에 받아 적기 시간을 더한 값. */
const POLL_LIMIT_MS = 3 * 60_000;
/** 요청 하나가 이 안에 대답하지 않으면 끊는다. 상태 묻기는 끊긴 뒤 다음 차례에 다시 묻는다. */
const REQUEST_TIMEOUT_MS = 10_000;
/** 녹음 올리기는 25MB를 느린 휴대폰 데이터로 보낼 수 있어 길게 둔다. */
const UPLOAD_TIMEOUT_MS = 2 * 60_000;

/** BE에 입장권을 붙여 요청한다. 약속은 contract.ts, 요청 방식은 callServer가 맡는다. */
export function httpServer(token: string): Server {
  return {
    pollLimitMs: POLL_LIMIT_MS,

    fetchResult: () =>
      callServer(token, RESULT_API, {
        ok: ResultView,
        errors: { 404: "not_found" },
        fallback: "failed",
        timeoutMs: REQUEST_TIMEOUT_MS,
      }),

    async uploadRecording(file) {
      const body = new FormData();
      body.append("file", file);
      const result = await callServer(token, CASES_API, {
        method: "POST",
        body,
        ok: CaseCreated,
        errors: { 409: "used" },
        errorBodies: { 400: FileRejection },
        fallback: "send_failed",
        timeoutMs: UPLOAD_TIMEOUT_MS,
      });
      return result.ok ? { ok: true, data: result.data.case_id } : result;
    },

    async fetchCaseStatus(caseId) {
      const result = await callServer(token, `${CASES_API}/${encodeURIComponent(caseId)}`, {
        ok: CaseStatusResponse,
        fallback: "failed",
        timeoutMs: REQUEST_TIMEOUT_MS,
      });
      return result.ok ? { ok: true, data: result.data.status } : result;
    },
  };
}
