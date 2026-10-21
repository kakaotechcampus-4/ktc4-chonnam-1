import type { FileProblem } from "../contract";

// 설계 문서 §9 B4 값. 서버도 다시 확인한다.
export const AUDIO_EXTENSIONS = ["m4a", "mp3", "wav", "webm"] as const;
export const MAX_AUDIO_BYTES = 25 * 1024 * 1024;

/**
 * 올릴 수 있는 녹음인지 본다. 문제가 없으면 null.
 * 휴대폰마다 파일 종류(MIME)를 다르게 주거나 비워서 주므로 확장자로 판단한다.
 */
export function checkAudioFile(file: File): FileProblem | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot > 0 ? file.name.slice(dot + 1).toLowerCase() : "";
  if (!(AUDIO_EXTENSIONS as readonly string[]).includes(extension)) return "unsupported_type";
  if (file.size === 0) return "empty";
  if (file.size > MAX_AUDIO_BYTES) return "too_large";
  return null;
}
