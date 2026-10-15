import type { Guidance } from "../types";
import { Section } from "./Section";
import styles from "./GuidanceList.module.css";

/** 공식 확인 방법. 카드와 달리 자르지 않고 전문을 보여 준다. 자료가 없으면 칸을 만들지 않는다. */
export function GuidanceList({ items }: { items: Guidance[] }) {
  if (items.length === 0) return null;
  return (
    <Section title="공식 확인 방법">
      {items.map((item) => (
        <article key={item.text} className={styles.item}>
          <p className={styles.source}>
            {item.year ? `${item.source} (${item.year})` : item.source}
          </p>
          <p className={styles.text}>{item.text}</p>
        </article>
      ))}
    </Section>
  );
}
