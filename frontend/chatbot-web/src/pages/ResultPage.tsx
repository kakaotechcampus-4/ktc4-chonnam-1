import { useHashToken } from "../hooks/useHashToken";
import { useResult } from "../hooks/useResult";
import { formatTime } from "../lib/time";
import { OriginalMessage } from "../components/OriginalMessage";
import { LineList } from "../components/LineList";
import { Section } from "../components/Section";
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
  const analyzed = formatTime(view.analyzed_at);
  const expires = formatTime(view.expires_at);

  return (
    <div className={styles.page}>
      {/* 결론과 다음 행동. 카드와 같은 문장이다. 이것만 읽어도 대응할 수 있어야 한다. */}
      <section className={`${styles.verdict} ${styles[view.tone]}`}>
        <h1 className={styles.headline}>{view.headline}</h1>
        <div className={styles.actions}>
          {view.action_lines.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      </section>

      <OriginalMessage messageText={view.message_text} highlights={view.highlights} />
      <LineList title="확인한 것" lines={view.checked} />
      <LineList title="확인하지 못한 것" lines={view.unchecked} />

      {/* 공식 확인 방법. 카드와 달리 자르지 않고 전문을 보여 준다. 자료가 없으면 칸을 만들지 않는다. */}
      {view.guidance.length > 0 && (
        <Section title="공식 확인 방법">
          {view.guidance.map((item) => (
            <article key={item.text} className={styles.guidance}>
              <p className={styles.source}>
                {item.year ? `${item.source} (${item.year})` : item.source}
              </p>
              <p className={styles.guidanceText}>{item.text}</p>
            </article>
          ))}
        </Section>
      )}

      {/* 확인 시각과 열람 기한. 신고·상담은 챗봇 카드 버튼으로 안내한다. */}
      <footer className={styles.footer}>
        {analyzed && <p>{analyzed}에 확인한 결과예요.</p>}
        {expires && <p>이 페이지는 {expires}까지만 열려요.</p>}
        <p>신고나 상담은 카카오톡 챗봇으로 돌아가 결과 카드의 버튼을 눌러 주세요.</p>
      </footer>
    </div>
  );
}
