import { formatTime } from "../lib/time";
import styles from "./ResultFooter.module.css";

interface ResultFooterProps {
  analyzedAt: string;
  expiresAt: string;
}

/** 확인 시각과 열람 기한. 신고·상담은 챗봇 카드 버튼으로 안내한다. */
export function ResultFooter({ analyzedAt, expiresAt }: ResultFooterProps) {
  const analyzed = formatTime(analyzedAt);
  const expires = formatTime(expiresAt);
  return (
    <footer className={styles.footer}>
      {analyzed && <p>{analyzed}에 확인한 결과예요.</p>}
      {expires && <p>이 페이지는 {expires}까지만 열려요.</p>}
      <p>신고나 상담은 카카오톡 챗봇으로 돌아가 결과 카드의 버튼을 눌러 주세요.</p>
    </footer>
  );
}
