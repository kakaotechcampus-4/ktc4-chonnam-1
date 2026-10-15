import type { Highlight } from "../types";
import styles from "./HighlightNotes.module.css";

const KIND_LABEL: Record<Highlight["kind"], string> = {
  claim: "문자가 주장한 것",
  request: "문자가 요구한 것",
  signal: "확인된 위험 근거",
};

interface HighlightNotesProps {
  highlights: Highlight[];
  /** 원문에서 실제로 칠한 인용의 번호. 칠하지 못한 인용은 설명도 뺀다. */
  shown: ReadonlySet<number>;
}

/** 칠한 구절마다 무엇인지 설명한다. 번호는 원문의 번호와 같다. */
export function HighlightNotes({ highlights, shown }: HighlightNotesProps) {
  if (shown.size === 0) return null;
  return (
    <ol className={styles.notes}>
      {highlights.map((highlight, index) =>
        shown.has(index) ? (
          <li key={index} value={index + 1}>
            <div className={styles.head}>
              <span className={`${styles.chip} ${styles[highlight.kind]}`}>
                {KIND_LABEL[highlight.kind]}
              </span>
              <strong>{highlight.label}</strong>
            </div>
            <p className={styles.note}>{highlight.note}</p>
          </li>
        ) : null,
      )}
    </ol>
  );
}
