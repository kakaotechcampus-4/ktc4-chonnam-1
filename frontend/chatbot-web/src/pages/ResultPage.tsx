import { useHashToken } from "../hooks/useHashToken";
import { useResult } from "../hooks/useResult";
import { Verdict } from "../components/Verdict";
import { OriginalMessage } from "../components/OriginalMessage";
import { LineList } from "../components/LineList";
import { GuidanceList } from "../components/GuidanceList";
import { ResultFooter } from "../components/ResultFooter";
import { ErrorState } from "../components/ErrorState";
import styles from "./ResultPage.module.css";

/** 결과 상세 페이지. 판정·해석은 하지 않고 받은 문장과 인용을 배치만 한다(frontend/README.md). */
export function ResultPage() {
  const token = useHashToken();
  const state = useResult(token);

  if (state.status === "loading") {
    return <p className={styles.loading}>결과를 불러오고 있어요…</p>;
  }
  if (state.status === "error") {
    return <ErrorState kind={state.error} />;
  }

  const { view } = state;
  return (
    <div className={styles.page}>
      <Verdict tone={view.tone} headline={view.headline} actionLines={view.action_lines} />
      <OriginalMessage messageText={view.message_text} highlights={view.highlights} />
      <LineList title="확인한 것" lines={view.checked} />
      <LineList title="확인하지 못한 것" lines={view.unchecked} />
      <GuidanceList items={view.guidance} />
      <ResultFooter analyzedAt={view.analyzed_at} expiresAt={view.expires_at} />
    </div>
  );
}
