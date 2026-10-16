import type { Highlight } from "../types";
import { markMessage } from "../lib/markMessage";
import { Section } from "./Section";
import styles from "./OriginalMessage.module.css";

const KIND_LABEL: Record<Highlight["kind"], string> = {
  claim: "문자가 주장한 것",
  request: "문자가 요구한 것",
  signal: "확인된 위험 근거",
};

interface OriginalMessageProps {
  messageText: string;
  highlights: Highlight[];
}

/** 받은 문자: 무해화한 원문 + 근거 강조 + 번호별 설명. 글은 React가 이스케이프한다. */
export function OriginalMessage({ messageText, highlights }: OriginalMessageProps) {
  const { pieces, notes } = markMessage(messageText, highlights);

  return (
    <Section title="받은 문자">
      <div className={styles.text}>
        {pieces.map((piece) =>
          "number" in piece ? (
            <mark key={piece.number} className={`${styles.mark} ${styles[piece.kind]}`}>
              {piece.text}
              <sup className={styles.badge}>{piece.number}</sup>
            </mark>
          ) : (
            piece.text
          ),
        )}
      </div>
      {notes.length > 0 && (
        <ol className={styles.notes}>
          {notes.map(({ number, highlight }) => (
            <li key={number} value={number}>
              <div className={styles.head}>
                <span className={`${styles.chip} ${styles[highlight.kind]}`}>
                  {KIND_LABEL[highlight.kind]}
                </span>
                <strong>{highlight.label}</strong>
              </div>
              <p className={styles.note}>{highlight.note}</p>
            </li>
          ))}
        </ol>
      )}
      <p className={styles.hint}>
        주소는 눌리지 않게 바꿔서 보여 줘요. 문자 속 링크와 번호는 사용하지 마세요.
      </p>
    </Section>
  );
}
