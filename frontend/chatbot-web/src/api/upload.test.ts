import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchCaseStatus, uploadRecording } from "./upload";

const recording = new File(["0000"], "call.m4a", { type: "audio/mp4" });

function respond(body: unknown, status = 200) {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(new Response(body === "" ? "" : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadRecording", () => {
  it("파일 하나를 multipart로 보내고 입장권은 주소가 아니라 헤더로 보낸다", async () => {
    const fetchMock = respond({ case_id: "c-1" }, 202);

    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({ ok: true, data: "c-1" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/cases");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ Authorization: "Bearer x7Kq" });
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("omit");
    const body = init.body as FormData;
    expect([...body.keys()]).toEqual(["file"]);
    expect((body.get("file") as File).name).toBe("call.m4a");
  });

  it("서버가 파일을 거절하면 그 이유를 돌려준다", async () => {
    respond({ error: "too_large" }, 400);
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({
      ok: false,
      error: "too_large",
    });
  });

  it("모르는 거절 이유는 보내기 실패로 본다", async () => {
    respond({ error: "virus" }, 400);
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
  });

  it("입장권이 틀렸거나 만료면(401·410) 만료로 본다", async () => {
    respond("", 401);
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({
      ok: false,
      error: "expired",
    });
    respond("", 410);
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({
      ok: false,
      error: "expired",
    });
  });

  it("이미 올린 입장권이면(409) 이미 올림으로 본다", async () => {
    respond("", 409);
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({ ok: false, error: "used" });
  });

  it("서버 오류, 인터넷 끊김, 빠진 case_id는 보내기 실패로 본다", async () => {
    respond("", 500);
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
    respond({}, 202);
    await expect(uploadRecording("x7Kq", recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
  });
});

describe("fetchCaseStatus", () => {
  it("상태만 받아 온다", async () => {
    const fetchMock = respond({ status: "running" });

    await expect(fetchCaseStatus("x7Kq", "c/1")).resolves.toEqual({ ok: true, data: "running" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/cases/c%2F1");
    expect(init.headers).toEqual({ Authorization: "Bearer x7Kq" });
    expect(init.cache).toBe("no-store");
  });

  it("질문을 기다리는 상태도 그대로 돌려준다", async () => {
    respond({ status: "waiting_answer" });
    await expect(fetchCaseStatus("x7Kq", "c-1")).resolves.toEqual({
      ok: true,
      data: "waiting_answer",
    });
  });

  it("입장권이 만료되면 만료로 본다", async () => {
    respond("", 410);
    await expect(fetchCaseStatus("x7Kq", "c-1")).resolves.toEqual({ ok: false, error: "expired" });
  });

  it("모르는 상태, 서버 오류, 인터넷 끊김은 실패로 본다", async () => {
    respond({ status: "safe" });
    await expect(fetchCaseStatus("x7Kq", "c-1")).resolves.toEqual({ ok: false, error: "failed" });
    respond("", 503);
    await expect(fetchCaseStatus("x7Kq", "c-1")).resolves.toEqual({ ok: false, error: "failed" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(fetchCaseStatus("x7Kq", "c-1")).resolves.toEqual({ ok: false, error: "failed" });
  });
});
