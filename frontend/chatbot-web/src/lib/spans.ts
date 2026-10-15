export interface Span {
  start: number;
  end: number;
  /** 몇 번째 인용인지 (highlights 배열의 순서) */
  index: number;
}

/** 원문에서 인용 위치를 찾는다. 겹치는 구절은 먼저 나온 것만 칠한다. 원문에 없는 인용은 칠하지 않는다. */
export function findSpans(text: string, quotes: string[]): Span[] {
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
