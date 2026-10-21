import { describe, expect, it } from "vitest";
import { checkAudioFile, MAX_AUDIO_BYTES } from "./audioFile";

function fileOf(name: string, size: number, type = ""): File {
  const file = new File([], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("checkAudioFile", () => {
  it("m4a·mp3·wav·webm 녹음은 통과시킨다", () => {
    for (const name of ["call.m4a", "call.mp3", "call.wav", "call.webm"]) {
      expect(checkAudioFile(fileOf(name, 1_000_000))).toBeNull();
    }
  });

  it("휴대폰이 파일 종류를 비워 보내도 확장자로 판단한다", () => {
    expect(checkAudioFile(fileOf("통화 녹음 010-1234-5678_20261021.m4a", 900_000, ""))).toBeNull();
  });

  it("확장자 대소문자는 가리지 않는다", () => {
    expect(checkAudioFile(fileOf("CALL.MP3", 1_000_000))).toBeNull();
  });

  it("다른 형식은 막는다", () => {
    expect(checkAudioFile(fileOf("call.amr", 1_000_000))).toBe("unsupported_type");
    expect(checkAudioFile(fileOf("photo.jpg", 1_000_000, "image/jpeg"))).toBe("unsupported_type");
    expect(checkAudioFile(fileOf("call.m4a.exe", 1_000_000))).toBe("unsupported_type");
    expect(checkAudioFile(fileOf("m4a", 1_000_000))).toBe("unsupported_type");
  });

  it("25MB까지는 받고 넘으면 막는다", () => {
    expect(MAX_AUDIO_BYTES).toBe(26_214_400);
    expect(checkAudioFile(fileOf("call.wav", 26_214_400))).toBeNull();
    expect(checkAudioFile(fileOf("call.wav", 26_214_401))).toBe("too_large");
  });

  it("빈 파일은 막는다", () => {
    expect(checkAudioFile(fileOf("call.m4a", 0))).toBe("empty");
  });
});
