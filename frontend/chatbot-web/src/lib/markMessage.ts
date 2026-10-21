// 받은 문자에 근거 인용을 표시한다 (ADR 0006).
// 원문과 인용을 같은 방식으로 무해화한 뒤 찾아서, 칠한 구절과 설명의 번호가 늘 맞게 한다.

import type { Highlight } from "../contract";
import { defang } from "./defang";

/** 원문 조각. 칠한 조각은 화면 번호(인용 순서 + 1)와 종류를 가진다. */
export type Piece = { text: string } | { text: string; number: number; kind: Highlight["kind"] };

export interface Note {
  number: number;
  highlight: Highlight;
}

export interface MarkedMessage {
  /** 원문 순서. 이어 붙이면 무해화한 원문이 된다. */
  pieces: Piece[];
  /** 칠한 인용만, 번호 순. 원문에 없거나 겹쳐서 칠하지 못한 인용은 뺀다. */
  notes: Note[];
}

interface Span {
  start: number;
  end: number;
  /** 몇 번째 인용인지 (highlights 배열의 순서) */
  index: number;
}

/** 원문에서 인용 위치를 찾는다. 겹치는 구절은 먼저 나온 것만, 같은 인용은 첫 자리만 남긴다. */
function findSpans(text: string, quotes: string[]): Span[] {
  const spans: Span[] = [];
  quotes.forEach((quote, index) => {
    if (!quote) return;
    const start = text.indexOf(quote);
    if (start < 0) return;
    spans.push({ start, end: start + quote.length, index });
  });
  spans.sort((a, b) => a.start - b.start);
  const kept: Span[] = [];
  for (const span of spans) {
    const last = kept[kept.length - 1];
    if (!last || span.start >= last.end) kept.push(span);
  }
  return kept;
}

/** 원문 → 무해화한 조각 목록 + 칠한 인용의 설명. 판정은 하지 않고 받은 인용만 표시한다. */
export function markMessage(messageText: string, highlights: Highlight[]): MarkedMessage {
  const text = defang(messageText);
  const spans = findSpans(
    text,
    highlights.map((highlight) => defang(highlight.quote)),
  );

  const pieces: Piece[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) pieces.push({ text: text.slice(cursor, span.start) });
    pieces.push({
      text: text.slice(span.start, span.end),
      number: span.index + 1,
      kind: highlights[span.index].kind,
    });
    cursor = span.end;
  }
  if (cursor < text.length) pieces.push({ text: text.slice(cursor) });

  const notes = [...spans]
    .sort((a, b) => a.index - b.index)
    .map((span) => ({ number: span.index + 1, highlight: highlights[span.index] }));
  return { pieces, notes };
}
