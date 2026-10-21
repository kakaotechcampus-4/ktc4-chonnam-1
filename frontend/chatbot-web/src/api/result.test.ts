import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchResult } from "./result";
import caution from "../../public/mock/caution.json";

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

describe("fetchResult", () => {
  it("입장권을 헤더로 보내 결과를 받는다", async () => {
    const fetchMock = respond(caution);

    await expect(fetchResult("x7Kq")).resolves.toEqual({ ok: true, data: caution });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/result");
    expect(init.headers).toEqual({ Authorization: "Bearer x7Kq" });
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("omit");
  });

  it("모르는 필드는 버린다", async () => {
    respond({ ...caution, verdict: "safe" });
    const result = await fetchResult("x7Kq");
    expect(result.ok && "verdict" in result.data).toBe(false);
  });

  it("입장권이 틀렸거나 만료면(401, 410) 만료, 결과가 사라졌으면(404) 없음", async () => {
    respond("", 401);
    await expect(fetchResult("x7Kq")).resolves.toEqual({ ok: false, error: "expired" });
    respond("", 410);
    await expect(fetchResult("x7Kq")).resolves.toEqual({ ok: false, error: "expired" });
    respond("", 404);
    await expect(fetchResult("x7Kq")).resolves.toEqual({ ok: false, error: "not_found" });
  });

  it("서버 오류와 인터넷 끊김은 실패", async () => {
    respond("", 500);
    await expect(fetchResult("x7Kq")).resolves.toEqual({ ok: false, error: "failed" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(fetchResult("x7Kq")).resolves.toEqual({ ok: false, error: "failed" });
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
        await expect(fetchResult("x7Kq")).resolves.toEqual({ ok: false, error: "failed" });
      });
    }
  });

  it("공식 확인 방법의 year는 null도 받는다", async () => {
    const guidance = [{ ...caution.guidance[0], year: null }];
    respond({ ...caution, guidance });
    const result = await fetchResult("x7Kq");
    expect(result.ok && result.data.guidance[0].year).toBeNull();
  });
});
