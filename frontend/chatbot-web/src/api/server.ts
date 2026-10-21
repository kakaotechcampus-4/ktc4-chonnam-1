import type { CaseStatus, LoadError, ResultView, StatusError, UploadError } from "../contract";
import type { Result } from "./callServer";
import { demoServer } from "./demoServer";
import { httpServer } from "./httpServer";

/**
 * 웹이 BE에 바라는 일. 입장권은 만들 때 한 번 넣는다.
 * 진짜 서버(httpServer)와 가짜 서버(demoServer)가 같은 모양이라, 부르는 쪽은 어느 쪽인지 모른다.
 */
export interface Server {
  fetchResult(): Promise<Result<ResultView, Exclude<LoadError, "no_token">>>;
  uploadRecording(file: File): Promise<Result<string, UploadError>>;
  fetchCaseStatus(caseId: string): Promise<Result<CaseStatus, StatusError>>;
  /** 분석을 이만큼 기다려도 안 끝나면 묻기를 멈춘다. */
  pollLimitMs: number;
}

/** 프로토타입: `#t=demo-…` 입장권이면 가짜 서버를 쓴다. 배포 빌드에서 데모를 빼려면 이 갈래를 막는다. */
const DEMO_PREFIX = "demo-";

export function serverFor(token: string): Server {
  return token.startsWith(DEMO_PREFIX)
    ? demoServer(token.slice(DEMO_PREFIX.length))
    : httpServer(token);
}
