import type { Guidance, Highlight, ResultView } from "../types";

// TypeScript 타입은 실행할 때 서버 응답을 검사하지 않는다.
// 화면이 기대는 필드만 여기서 확인하고, 어긋나면 던진다(→ "불러오지 못했어요" 화면).

const TONES: readonly ResultView["tone"][] = ["lookalike", "caution", "uncertain"];
const KINDS: readonly Highlight["kind"][] = ["claim", "request", "signal"];

type Data = Record<string, unknown>;

function fail(field: string): never {
  throw new TypeError(`결과 응답의 ${field} 값이 올바르지 않아요`);
}

function asObject(value: unknown, field: string): Data {
  if (typeof value !== "object" || value === null || Array.isArray(value)) fail(field);
  return value as Data;
}

function asString(value: unknown, field: string): string {
  if (typeof value !== "string") fail(field);
  return value;
}

function asStringList(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) fail(field);
  return value.map((item, i) => asString(item, `${field}[${i}]`));
}

function asList<T>(value: unknown, field: string, read: (item: Data, field: string) => T): T[] {
  if (!Array.isArray(value)) fail(field);
  return value.map((item, i) => read(asObject(item, `${field}[${i}]`), `${field}[${i}]`));
}

function asOneOf<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) fail(field);
  return value as T;
}

function readHighlight(item: Data, field: string): Highlight {
  return {
    quote: asString(item.quote, `${field}.quote`),
    kind: asOneOf(item.kind, KINDS, `${field}.kind`),
    label: asString(item.label, `${field}.label`),
    note: asString(item.note, `${field}.note`),
  };
}

function readGuidance(item: Data, field: string): Guidance {
  const year = item.year;
  if (year !== null && typeof year !== "string") fail(`${field}.year`);
  return {
    source: asString(item.source, `${field}.source`),
    year,
    text: asString(item.text, `${field}.text`),
  };
}

/** 서버 응답을 ResultView로 확인한다. 필요한 필드만 골라 담아 모르는 필드는 버린다. */
export function parseResultView(raw: unknown): ResultView {
  const data = asObject(raw, "응답");
  return {
    tone: asOneOf(data.tone, TONES, "tone"),
    headline: asString(data.headline, "headline"),
    action_lines: asStringList(data.action_lines, "action_lines"),
    message_text: asString(data.message_text, "message_text"),
    highlights: asList(data.highlights, "highlights", readHighlight),
    checked: asStringList(data.checked, "checked"),
    unchecked: asStringList(data.unchecked, "unchecked"),
    guidance: asList(data.guidance, "guidance", readGuidance),
    analyzed_at: asString(data.analyzed_at, "analyzed_at"),
    expires_at: asString(data.expires_at, "expires_at"),
  };
}
