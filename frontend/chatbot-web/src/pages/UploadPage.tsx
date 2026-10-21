import type { ChangeEvent } from "react";
import type { FileProblem } from "../contract";
import { useHashToken } from "../hooks/useHashToken";
import { useUpload } from "../hooks/useUpload";
import { AUDIO_EXTENSIONS } from "../lib/audioFile";
import { Notice } from "../components/Notice";
import styles from "./UploadPage.module.css";

const PROBLEM_COPY: Record<FileProblem | "send_failed", string> = {
  unsupported_type: "m4a, mp3, wav, webm 파일만 올릴 수 있어요.",
  too_large: "25MB 이하 파일만 올릴 수 있어요.",
  empty: "빈 파일이에요. 다른 녹음을 골라 주세요.",
  send_failed: "보내지 못했어요. 인터넷 연결을 확인하고 다시 보내 주세요.",
};

// 휴대폰 파일 선택 창에 녹음 파일이 보이게 한다. 실제 판단은 확장자로 한다(checkAudioFile).
const ACCEPT = [...AUDIO_EXTENSIONS.map((ext) => `.${ext}`), "audio/*"].join(",");

/**
 * 통화 녹음 업로드 페이지(이슈 #74). 올리기 → 분석 중 → "끝났어요"까지만 한다.
 * 결과와 질문은 웹에 보여 주지 않고 챗봇 카드로 보여 준다(chatbot-copy.md 1절 D10).
 */
export function UploadPage() {
  const token = useHashToken();
  const { state, canSend, choose, send } = useUpload(token);

  switch (state.phase) {
    case "expired":
      return (
        <Notice title="올리기 화면을 열 수 없어요" role="alert">
          <p>시간이 지났거나 주소가 잘못됐어요. 챗봇에서 [녹음 올리기]를 다시 눌러 주세요.</p>
        </Notice>
      );
    case "used":
      return (
        <Notice title="이미 올린 녹음이 있어요" role="alert">
          <p>분석이 끝나면 챗봇에서 '결과 확인'을 눌러 확인해 주세요.</p>
        </Notice>
      );
    case "analyzing":
      return (
        <Notice title="녹음을 분석하고 있어요" role="status">
          <span className={styles.spinner} aria-hidden="true" />
          <p>1~2분쯤 걸려요. 이 화면을 닫아도 분석은 계속돼요.</p>
        </Notice>
      );
    case "finished":
      return (
        <Notice title="분석이 끝났어요" role="status">
          <p>챗봇으로 돌아가 '결과 확인'을 눌러 주세요.</p>
        </Notice>
      );
    case "slow":
      return (
        <Notice title="시간이 걸리고 있어요" role="status">
          <p>잠시 뒤 챗봇에서 '결과 확인'을 눌러 주세요.</p>
        </Notice>
      );
    case "analysis_failed":
      return (
        <Notice title="녹음을 분석하지 못했어요" role="alert">
          <p>챗봇에서 [녹음 올리기]를 다시 눌러 올려 주세요.</p>
        </Notice>
      );
    case "choosing":
    case "sending":
      return (
        <ChooseForm
          file={state.file}
          fileProblem={state.phase === "choosing" ? state.fileProblem : null}
          sendFailed={state.phase === "choosing" && state.sendFailed}
          sending={state.phase === "sending"}
          canSend={canSend}
          onChoose={choose}
          onSend={send}
        />
      );
  }
}

function ChooseForm({
  file,
  fileProblem,
  sendFailed,
  sending,
  canSend,
  onChoose,
  onSend,
}: {
  file: File | null;
  fileProblem: FileProblem | null;
  sendFailed: boolean;
  sending: boolean;
  canSend: boolean;
  onChoose: (file: File) => void;
  onSend: () => void;
}) {
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0];
    // 선택 창을 그냥 닫으면 앞서 고른 파일을 그대로 둔다.
    if (chosen) onChoose(chosen);
  };
  const problem = fileProblem ?? (sendFailed ? "send_failed" : null);

  return (
    <form
      className={styles.page}
      onSubmit={(event) => {
        event.preventDefault();
        onSend();
      }}
    >
      <header className={styles.header}>
        <h1 className={styles.title}>🎙️ 통화 녹음 올리기</h1>
        <p className={styles.soft}>파일 1개, 25MB 이하 (m4a, mp3, wav, webm)</p>
      </header>

      <section className={styles.panel}>
        <label className={styles.pick}>
          파일 고르기
          <input
            className={styles.fileInput}
            type="file"
            accept={ACCEPT}
            disabled={sending}
            onChange={onChange}
          />
        </label>
        {file && (
          <p className={styles.fileName}>
            {file.name} ({formatMegabytes(file.size)})
          </p>
        )}
        {problem && (
          <p className={styles.problem} role="alert">
            {PROBLEM_COPY[problem]}
          </p>
        )}

        {/* 경로는 실기기 확인 전이다(README). */}
        <details className={styles.help}>
          <summary>녹음 파일을 못 찾겠어요</summary>
          <dl>
            <dt>갤럭시</dt>
            <dd>파일 고르기 → 내 파일 → Recordings → Call</dd>
            <dt>아이폰</dt>
            <dd>
              아이폰은 통화 녹음 파일이 '메모' 앱에 저장돼요. 메모에서 녹음을 길게 누르기 → 공유 →
              '파일에 저장'을 한 뒤 파일 고르기에서 골라 주세요.
            </dd>
          </dl>
        </details>
      </section>

      {/* 개인정보 안내. 보내기를 누르면 동의한 것으로 본다(시험 운영 문구, 팀과 다시 볼 것). */}
      <p className={styles.privacy}>
        보내기를 누르면 녹음을 분석하는 데 쓰는 것에 동의하는 거예요.
        <br />
        녹음은 받아 적은 뒤 분석이 끝나면 바로 지워요.
      </p>
      <button className={styles.send} type="submit" disabled={!canSend}>
        {sending ? "보내는 중…" : "보내기"}
      </button>
    </form>
  );
}

function formatMegabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
