import type { LoadError } from "../types";
import styles from "./ErrorState.module.css";

const ERROR_COPY: Record<LoadError, { title: string; body: string }> = {
  no_token: {
    title: "결과를 열 수 없어요",
    body: "카카오톡 챗봇의 결과 카드에서 [자세히 보기]를 다시 눌러 주세요.",
  },
  expired: {
    title: "열람 시간이 지났어요",
    body: "결과 페이지는 잠깐만 열려요. 챗봇에 「결과 확인」이라고 보내 다시 열어 주세요.",
  },
  not_found: {
    title: "결과를 찾을 수 없어요",
    body: "받은 문자를 챗봇에 다시 보내 확인해 주세요. 그동안 문자 속 링크는 누르지 마세요.",
  },
  failed: {
    title: "결과를 불러오지 못했어요",
    body: "잠시 뒤 다시 열어 주세요. 그동안 문자 속 링크는 누르지 마세요.",
  },
};

/** 입장권 없음·만료·실패 화면. 업로드 페이지도 같은 모양을 쓴다. */
export function ErrorState({ kind }: { kind: LoadError }) {
  const { title, body } = ERROR_COPY[kind];
  return (
    <section className={styles.state} role="alert">
      <h1 className={styles.title}>{title}</h1>
      <p>{body}</p>
    </section>
  );
}
