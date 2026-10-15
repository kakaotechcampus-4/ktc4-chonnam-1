import { ResultPage } from "./pages/ResultPage";
import styles from "./App.module.css";

// 지금은 결과 상세 페이지 하나다. 업로드 페이지(#74)가 생기면 여기서 나눈다.
export function App() {
  return (
    <main className={styles.app}>
      <ResultPage />
    </main>
  );
}
