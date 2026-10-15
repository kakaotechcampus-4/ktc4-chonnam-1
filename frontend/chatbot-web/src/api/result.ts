import type { LoadError, ResultView } from "../types";
import { DEMO_PREFIX, isDemo } from "../lib/token";
import { parseResultView } from "./parseResultView";

const RESULT_API = "/api/result";

/** 입장권으로 결과를 받는다. 입장권은 주소가 아니라 헤더로 보낸다. 실패하면 LoadError를 던진다. */
export async function fetchResult(token: string): Promise<ResultView> {
  const demo = isDemo(token);
  const url = demo ? `./mock/${token.slice(DEMO_PREFIX.length)}.json` : RESULT_API;
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "omit",
    headers: demo ? {} : { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401 || response.status === 404 || response.status === 410) {
    throw "expired" satisfies LoadError;
  }
  if (!response.ok) {
    throw "failed" satisfies LoadError;
  }
  try {
    return parseResultView(await response.json());
  } catch {
    throw "failed" satisfies LoadError;
  }
}
