import { Section } from "./Section";
import styles from "./LineList.module.css";

interface LineListProps {
  title: string;
  lines: string[];
}

/** "확인한 것"·"확인하지 못한 것"처럼 제목과 줄 목록. 줄이 없으면 "없음". */
export function LineList({ title, lines }: LineListProps) {
  const items = lines.length > 0 ? lines : ["없음"];
  return (
    <Section title={title}>
      <ul className={styles.lines}>
        {items.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </Section>
  );
}
