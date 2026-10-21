import type { CaseStatus, StatusError, UploadError } from "../contract";
import type { Result } from "./callServer";

// 프로토타입: `#t=demo-…`이면 서버 대신 이 가짜 응답을 쓴다. 올리기와 기다리기는 JSON 파일로 흉내 낼 수 없어 함수로 둔다.
// 이름: ok(몇 초 뒤 끝남) · fail(분석 실패) · slow(안 끝남) · expired(만료) · used(이미 올림) · offline(처음 한 번 보내기 실패)

const SEND_MS = 1_200;
const ANALYSIS_MS = 6_000;

const startedAt = new Map<string, number>();
let offlineFailed = false;

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function demoUpload(name: string): Promise<Result<string, UploadError>> {
  await delay(SEND_MS);
  if (name === "expired" || name === "used") return { ok: false, error: name };
  if (name === "offline" && !offlineFailed) {
    offlineFailed = true;
    return { ok: false, error: "send_failed" };
  }
  startedAt.set(name, Date.now());
  return { ok: true, data: name };
}

export async function demoStatus(caseId: string): Promise<Result<CaseStatus, StatusError>> {
  const started = startedAt.get(caseId);
  if (started === undefined) return { ok: false, error: "failed" };
  if (caseId === "slow" || Date.now() - started < ANALYSIS_MS) return { ok: true, data: "running" };
  return { ok: true, data: caseId === "fail" ? "failed" : "done" };
}
