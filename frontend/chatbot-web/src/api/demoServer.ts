import { ResultView } from "../contract";
import type { Server } from "./server";

// 프로토타입: 서버 없이 화면을 확인하는 가짜 서버. 이름은 입장권 `demo-` 뒤의 글자다.
// 결과: caution · lookalike · uncertain(public/mock/의 JSON) · expired(만료) · not-found(결과 없음)
// 업로드: ok(몇 초 뒤 끝남) · fail(분석 실패) · slow(안 끝남) · expired(만료) · used(이미 올림)
//         · offline(처음 한 번 보내기 실패)

const SEND_MS = 1_200;
const ANALYSIS_MS = 6_000;
/** `demo-slow`가 3분을 기다리지 않게 줄인다. */
const POLL_LIMIT_MS = 10_000;

const startedAt = new Map<string, number>();
let offlineFailed = false;

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function demoServer(name: string): Server {
  return {
    pollLimitMs: POLL_LIMIT_MS,

    async fetchResult() {
      if (name === "expired") return { ok: false, error: "expired" };
      if (name === "not-found") return { ok: false, error: "not_found" };
      try {
        const response = await fetch(`./mock/${name}.json`, { cache: "no-store" });
        const parsed = ResultView.safeParse(await response.json());
        return parsed.success ? { ok: true, data: parsed.data } : { ok: false, error: "failed" };
      } catch {
        return { ok: false, error: "failed" };
      }
    },

    async uploadRecording() {
      await delay(SEND_MS);
      if (name === "expired" || name === "used") return { ok: false, error: name };
      if (name === "offline" && !offlineFailed) {
        offlineFailed = true;
        return { ok: false, error: "send_failed" };
      }
      startedAt.set(name, Date.now());
      return { ok: true, data: name };
    },

    async fetchCaseStatus(caseId) {
      const started = startedAt.get(caseId);
      if (started === undefined) return { ok: false, error: "failed" };
      if (caseId === "slow" || Date.now() - started < ANALYSIS_MS) {
        return { ok: true, data: "running" };
      }
      return { ok: true, data: caseId === "fail" ? "failed" : "done" };
    },
  };
}
