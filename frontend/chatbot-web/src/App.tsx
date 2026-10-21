import type { ReactNode } from "react";
import styles from "./App.module.css";

/** 두 페이지(결과 `index.html`, 업로드 `upload/index.html`)가 같이 쓰는 바깥 틀. */
export function App({ children }: { children: ReactNode }) {
  return <main className={styles.app}>{children}</main>;
}
