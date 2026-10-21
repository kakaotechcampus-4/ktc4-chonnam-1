import type { ReactNode } from "react";
import styles from "./Notice.module.css";

/** 제목과 짧은 안내 한 칸. 오류는 alert, 진행 상태는 status로 읽힌다. 결과·업로드 페이지가 같이 쓴다. */
export function Notice({
  title,
  role,
  children,
}: {
  title: string;
  role: "alert" | "status";
  children: ReactNode;
}) {
  return (
    <section className={styles.state} role={role}>
      <h1 className={styles.title}>{title}</h1>
      {children}
    </section>
  );
}
