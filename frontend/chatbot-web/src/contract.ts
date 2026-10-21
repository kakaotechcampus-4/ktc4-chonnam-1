import { z } from "zod/mini";

// FE가 제안하는 BE 응답 약속 (합의 전). 모양을 여기서 한 번만 적고 타입은 모양에서 뽑는다.
// 화면이 기대는 필드만 확인하고, 모르는 필드는 버린다. 업로드 약속은 이슈 #74 코멘트에 있다.

// ── 결과 조회 `GET /api/result` ─────────────────────────────
// 문장은 카드와 같은 규칙으로 BE가 만든다.

/** 원문에서 강조할 구절. `quote`는 원문에 실제로 있는 인용(AI `evidence`)이다. */
export const Highlight = z.object({
  quote: z.string(),
  /** claim: 문자가 주장한 기관·명분, request: 요구 행동, signal: 검증된 위험 근거 */
  kind: z.enum(["claim", "request", "signal"]),
  label: z.string(),
  note: z.string(),
});
export type Highlight = z.infer<typeof Highlight>;

export const Guidance = z.object({
  source: z.string(),
  year: z.nullable(z.string()),
  text: z.string(),
});
export type Guidance = z.infer<typeof Guidance>;

export const ResultView = z.object({
  /** 카드와 같은 갈래: lookalike(R1) · caution(R9 주의) · uncertain(R9 판단 보류) */
  tone: z.enum(["lookalike", "caution", "uncertain"]),
  headline: z.string(),
  action_lines: z.array(z.string()),
  /** 문자 원문. 지금 서버는 원문을 저장하지 않아 BE 저장이 필요하다. */
  message_text: z.string(),
  highlights: z.array(Highlight),
  checked: z.array(z.string()),
  unchecked: z.array(z.string()),
  guidance: z.array(Guidance),
  analyzed_at: z.string(),
  expires_at: z.string(),
});
export type ResultView = z.infer<typeof ResultView>;

/** expired: 입장권 만료·무효(401·410), not_found: 결과가 사라짐(404, 서버 재시작 등) */
export type LoadError = "no_token" | "expired" | "not_found" | "failed";

// ── 녹음 올리기 `POST /api/cases`, 상태 조회 `GET /api/cases/{case_id}` ──

/** 웹이 미리 막는 파일 문제. 서버도 같은 이름으로 400 `{error}`를 준다. */
export const FileProblem = z.enum(["unsupported_type", "too_large", "empty"]);
export type FileProblem = z.infer<typeof FileProblem>;

/** 400 본문에서 거절 이유만 꺼낸다. */
export const FileRejection = z.pipe(
  z.object({ error: FileProblem }),
  z.transform((body) => body.error),
);

export const CaseCreated = z.object({ case_id: z.string().check(z.minLength(1)) });

/** 상태 조회 응답은 `status`뿐이다. 웹은 결과·질문 내용을 받지 않는다(카드로만 보여 준다). */
export const CaseStatus = z.enum(["running", "waiting_answer", "done", "failed", "expired"]);
export type CaseStatus = z.infer<typeof CaseStatus>;
export const CaseStatusResponse = z.object({ status: CaseStatus });

/** 올리기 실패. used: 이 입장권으로 이미 올림(409), send_failed: 서버 오류·인터넷 끊김(다시 보낼 수 있음) */
export type UploadError = FileProblem | "expired" | "used" | "send_failed";

/** expired: 입장권 만료(401·410), failed: 그 밖의 조회 실패(다음 차례에 다시 묻는다) */
export type StatusError = "expired" | "failed";
