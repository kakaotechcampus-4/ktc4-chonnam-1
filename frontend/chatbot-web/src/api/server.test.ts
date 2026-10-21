import { afterEach, describe, expect, it, vi } from "vitest";
import { serverFor } from "./server";
import caution from "../../public/mock/caution.json";

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

describe("serverFor", () => {
  it("데모 입장권이 아니면 진짜 서버를 쓰고 3분까지 기다린다", () => {
    expect(serverFor("x7Kq").pollLimitMs).toBe(3 * 60_000);
  });

  it("데모 입장권이면 가짜 서버를 쓰고 BE에 요청하지 않는다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(caution)));
    vi.stubGlobal("fetch", fetchMock);
    const server = serverFor("demo-expired");
    expect(server.pollLimitMs).toBeLessThan(60_000);
    await expect(server.fetchResult()).resolves.toEqual({ ok: false, error: "expired" });
    expect(fetchMock.mock.calls.map(([url]) => url).join()).not.toContain("/api/");
  });
});

describe("fetchResult (진짜 서버)", () => {
  it("입장권을 헤더로 보내 결과를 받는다", async () => {
    const fetchMock = respond(caution);

    await expect(serverFor("x7Kq").fetchResult()).resolves.toEqual({ ok: true, data: caution });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/result");
    expect(init.headers).toEqual({ Authorization: "Bearer x7Kq" });
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("omit");
  });

  it("모르는 필드는 버린다", async () => {
    respond({ ...caution, verdict: "safe" });
    const result = await serverFor("x7Kq").fetchResult();
    expect(result.ok && "verdict" in result.data).toBe(false);
  });

  it("입장권이 틀렸거나 만료면(401, 410) 만료, 결과가 사라졌으면(404) 없음", async () => {
    respond("", 401);
    await expect(serverFor("x7Kq").fetchResult()).resolves.toEqual({ ok: false, error: "expired" });
    respond("", 410);
    await expect(serverFor("x7Kq").fetchResult()).resolves.toEqual({ ok: false, error: "expired" });
    respond("", 404);
    await expect(serverFor("x7Kq").fetchResult()).resolves.toEqual({
      ok: false,
      error: "not_found",
    });
  });

  it("서버 오류와 인터넷 끊김은 실패", async () => {
    respond("", 500);
    await expect(serverFor("x7Kq").fetchResult()).resolves.toEqual({ ok: false, error: "failed" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(serverFor("x7Kq").fetchResult()).resolves.toEqual({ ok: false, error: "failed" });
  });

  describe("응답 모양이 어긋나면 실패", () => {
    const broken: Record<string, unknown> = {
      "JSON이 아님": "<html>",
      "객체가 아님": null,
      "모르는 tone": { ...caution, tone: "safe" },
      "빠진 headline": { ...caution, headline: undefined },
      "문자열 목록에 숫자": { ...caution, checked: ["확인", 1] },
      "모르는 강조 종류": {
        ...caution,
        highlights: [{ ...caution.highlights[0], kind: "verdict" }],
      },
      "숫자 year": { ...caution, guidance: [{ ...caution.guidance[0], year: 2025 }] },
    };
    for (const [name, body] of Object.entries(broken)) {
      it(name, async () => {
        if (name === "JSON이 아님") {
          vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>")));
        } else {
          respond(body);
        }
        await expect(serverFor("x7Kq").fetchResult()).resolves.toEqual({
          ok: false,
          error: "failed",
        });
      });
    }
  });

  it("공식 확인 방법의 year는 null도 받는다", async () => {
    const guidance = [{ ...caution.guidance[0], year: null }];
    respond({ ...caution, guidance });
    const result = await serverFor("x7Kq").fetchResult();
    expect(result.ok && result.data.guidance[0].year).toBeNull();
  });
});

describe("uploadRecording (진짜 서버)", () => {
  it("파일 하나를 multipart로 보내고 입장권은 주소가 아니라 헤더로 보낸다", async () => {
    const fetchMock = respond({ case_id: "c-1" }, 202);

    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: true,
      data: "c-1",
    });

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
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "too_large",
    });
  });

  it("모르는 거절 이유는 보내기 실패로 본다", async () => {
    respond({ error: "virus" }, 400);
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
  });

  it("입장권이 틀렸거나 만료면(401·410) 만료로 본다", async () => {
    respond("", 401);
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "expired",
    });
    respond("", 410);
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "expired",
    });
  });

  it("이미 올린 입장권이면(409) 이미 올림으로 본다", async () => {
    respond("", 409);
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "used",
    });
  });

  it("서버 오류, 인터넷 끊김, 빠진 case_id는 보내기 실패로 본다", async () => {
    respond("", 500);
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
    respond({}, 202);
    await expect(serverFor("x7Kq").uploadRecording(recording)).resolves.toEqual({
      ok: false,
      error: "send_failed",
    });
  });
});

describe("fetchCaseStatus (진짜 서버)", () => {
  it("상태만 받아 온다", async () => {
    const fetchMock = respond({ status: "running" });

    await expect(serverFor("x7Kq").fetchCaseStatus("c/1")).resolves.toEqual({
      ok: true,
      data: "running",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/cases/c%2F1");
    expect(init.headers).toEqual({ Authorization: "Bearer x7Kq" });
    expect(init.cache).toBe("no-store");
  });

  it("질문을 기다리는 상태도 그대로 돌려준다", async () => {
    respond({ status: "waiting_answer" });
    await expect(serverFor("x7Kq").fetchCaseStatus("c-1")).resolves.toEqual({
      ok: true,
      data: "waiting_answer",
    });
  });

  it("입장권이 만료되면 만료로 본다", async () => {
    respond("", 410);
    await expect(serverFor("x7Kq").fetchCaseStatus("c-1")).resolves.toEqual({
      ok: false,
      error: "expired",
    });
  });

  it("모르는 상태, 서버 오류, 인터넷 끊김은 실패로 본다", async () => {
    respond({ status: "safe" });
    await expect(serverFor("x7Kq").fetchCaseStatus("c-1")).resolves.toEqual({
      ok: false,
      error: "failed",
    });
    respond("", 503);
    await expect(serverFor("x7Kq").fetchCaseStatus("c-1")).resolves.toEqual({
      ok: false,
      error: "failed",
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(serverFor("x7Kq").fetchCaseStatus("c-1")).resolves.toEqual({
      ok: false,
      error: "failed",
    });
  });
});

describe("대답이 오지 않을 때 (진짜 서버)", () => {
  /** 끊으라는 신호(abort)를 받을 때까지 대답하지 않는 서버 */
  function silentServer() {
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  afterEach(() => {
    vi.useRealTimers();
  });

  it("상태 조회는 10초 안에 대답이 없으면 끊고 실패로 본다", async () => {
    vi.useFakeTimers();
    silentServer();
    const pending = serverFor("x7Kq").fetchCaseStatus("c-1");
    await vi.advanceTimersByTimeAsync(9_999);
    let settled = false;
    void pending.then(() => (settled = true));
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toEqual({ ok: false, error: "failed" });
  });

  it("결과 조회도 10초 안에 대답이 없으면 실패로 본다", async () => {
    vi.useFakeTimers();
    silentServer();
    const pending = serverFor("x7Kq").fetchResult();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(pending).resolves.toEqual({ ok: false, error: "failed" });
  });

  it("녹음 올리기는 2분까지 기다리고, 그래도 대답이 없으면 보내기 실패로 본다", async () => {
    vi.useFakeTimers();
    silentServer();
    const pending = serverFor("x7Kq").uploadRecording(recording);
    let settled = false;
    void pending.then(() => (settled = true));
    await vi.advanceTimersByTimeAsync(119_999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toEqual({ ok: false, error: "send_failed" });
  });
});
