import type { ReactNode } from "react";
import styles from "./Section.module.css";

interface SectionProps {
  title: string;
  children: ReactNode;
}

/** 제목이 있는 흰 상자. 결과 페이지의 칸들이 같이 쓴다. */
export function Section({ title, children }: SectionProps) {
  return (
    <section className={styles.section}>
      <h2 className={styles.title}>{title}</h2>
      {children}
    </section>
  );
}
