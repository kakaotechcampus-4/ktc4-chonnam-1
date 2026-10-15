import type { ReactNode } from "react";
import type { Highlight } from "../types";
import type { Span } from "../lib/spans";
import styles from "./HighlightedText.module.css";

interface HighlightedTextProps {
  text: string;
  highlights: Highlight[];
  spans: Span[];
}

/** 원문에 근거 구절을 칠하고 번호를 붙인다. 글은 React가 이스케이프한다. */
export function HighlightedText({ text, highlights, spans }: HighlightedTextProps) {
  const pieces: ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) pieces.push(text.slice(cursor, span.start));
    const { kind } = highlights[span.index];
    pieces.push(
      <mark key={span.index} className={`${styles.mark} ${styles[kind]}`}>
        {text.slice(span.start, span.end)}
        <sup className={styles.badge}>{span.index + 1}</sup>
      </mark>,
    );
    cursor = span.end;
  }
  if (cursor < text.length) pieces.push(text.slice(cursor));

  return <div className={styles.text}>{pieces}</div>;
}
