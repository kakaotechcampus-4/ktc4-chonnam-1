// 조회 API 응답 (FE 제안 계약, BE와 합의 전). 문장은 카드와 같은 규칙으로 BE가 만든다.

/** 원문에서 강조할 구절. `quote`는 원문에 실제로 있는 인용(AI `evidence`)이다. */
export interface Highlight {
  quote: string;
  /** claim: 문자가 주장한 기관·명분, request: 요구 행동, signal: 검증된 위험 근거 */
  kind: "claim" | "request" | "signal";
  label: string;
  note: string;
}

export interface Guidance {
  source: string;
  year: string | null;
  text: string;
}

export interface ResultView {
  /** 카드와 같은 갈래: lookalike(R1) · caution(R9 주의) · uncertain(R9 판단 보류) */
  tone: "lookalike" | "caution" | "uncertain";
  headline: string;
  action_lines: string[];
  /** 문자 원문. 지금 서버는 원문을 저장하지 않아 BE 저장이 필요하다. */
  message_text: string;
  highlights: Highlight[];
  checked: string[];
  unchecked: string[];
  guidance: Guidance[];
  analyzed_at: string;
  expires_at: string;
}

export type LoadError = "no_token" | "expired" | "failed";
