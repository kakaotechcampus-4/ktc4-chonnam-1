import type { ResultView } from "../types";
import styles from "./Verdict.module.css";

interface VerdictProps {
  tone: ResultView["tone"];
  headline: string;
  actionLines: string[];
}

/** 결론과 다음 행동. 카드와 같은 문장이다. 이것만 읽어도 대응할 수 있어야 한다. */
export function Verdict({ tone, headline, actionLines }: VerdictProps) {
  return (
    <section className={`${styles.verdict} ${styles[tone]}`}>
      <h1 className={styles.headline}>{headline}</h1>
      <div className={styles.actions}>
        {actionLines.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
    </section>
  );
}
