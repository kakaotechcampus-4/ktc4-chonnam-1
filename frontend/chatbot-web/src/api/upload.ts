import {
  CaseCreated,
  CaseStatusResponse,
  FileRejection,
  type CaseStatus,
  type StatusError,
  type UploadError,
} from "../contract";
import { DEMO_PREFIX, isDemo } from "../lib/token";
import { callServer, type Result } from "./callServer";
import { demoStatus, demoUpload } from "./demoUpload";

const CASES_API = "/api/cases";

/** 녹음을 올리고 case_id를 받는다. */
export async function uploadRecording(
  token: string,
  file: File,
): Promise<Result<string, UploadError>> {
  if (isDemo(token)) return demoUpload(token.slice(DEMO_PREFIX.length));
  const body = new FormData();
  body.append("file", file);
  const result = await callServer(token, CASES_API, {
    method: "POST",
    body,
    ok: CaseCreated,
    errors: { 409: "used" },
    errorBodies: { 400: FileRejection },
    fallback: "send_failed",
  });
  return result.ok ? { ok: true, data: result.data.case_id } : result;
}

/** 분석 상태만 받는다. */
export async function fetchCaseStatus(
  token: string,
  caseId: string,
): Promise<Result<CaseStatus, StatusError>> {
  if (isDemo(token)) return demoStatus(caseId);
  const result = await callServer(token, `${CASES_API}/${encodeURIComponent(caseId)}`, {
    ok: CaseStatusResponse,
    fallback: "failed",
  });
  return result.ok ? { ok: true, data: result.data.status } : result;
}
