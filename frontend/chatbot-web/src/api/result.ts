import { ResultView, type LoadError } from "../contract";
import { DEMO_PREFIX, isDemo } from "../lib/token";
import { callServer, type Result } from "./callServer";

const RESULT_API = "/api/result";

/** 프로토타입: 파일로는 401·404를 흉내 낼 수 없어 이름으로 오류 화면을 연다. */
const DEMO_ERRORS: Record<string, LoadError> = { expired: "expired", "not-found": "not_found" };

/** 입장권으로 결과를 받는다. */
export async function fetchResult(
  token: string,
): Promise<Result<ResultView, Exclude<LoadError, "no_token">>> {
  const demoName = token.slice(DEMO_PREFIX.length);
  const demo = isDemo(token);
  if (demo && demoName in DEMO_ERRORS) {
    return { ok: false, error: DEMO_ERRORS[demoName] as Exclude<LoadError, "no_token"> };
  }
  return callServer(token, demo ? `./mock/${demoName}.json` : RESULT_API, {
    ok: ResultView,
    errors: { 404: "not_found" },
    fallback: "failed",
  });
}
