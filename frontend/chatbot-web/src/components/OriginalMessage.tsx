import type { Highlight } from "../types";
import { defang } from "../lib/defang";
import { findSpans } from "../lib/spans";
import { Section } from "./Section";
import { HighlightedText } from "./HighlightedText";
import { HighlightNotes } from "./HighlightNotes";
import styles from "./OriginalMessage.module.css";

interface OriginalMessageProps {
  messageText: string;
  highlights: Highlight[];
}

/** 받은 문자: 무해화한 원문 + 근거 강조 + 번호별 설명. 원문에 없는 인용은 칠하지 않는다. */
export function OriginalMessage({ messageText, highlights }: OriginalMessageProps) {
  const text = defang(messageText);
  const spans = findSpans(
    text,
    highlights.map((highlight) => defang(highlight.quote)),
  );
  const shown = new Set(spans.map((span) => span.index));

  return (
    <Section title="받은 문자">
      <HighlightedText text={text} highlights={highlights} spans={spans} />
      <HighlightNotes highlights={highlights} shown={shown} />
      <p className={styles.hint}>
        주소는 눌리지 않게 바꿔서 보여 줘요. 문자 속 링크와 번호는 사용하지 마세요.
      </p>
    </Section>
  );
}
