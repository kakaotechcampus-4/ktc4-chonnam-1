import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResultPage } from "./ResultPage";
import caution from "../../public/mock/caution.json";

function openWithHash(hash: string) {
  window.history.replaceState(null, "", `/${hash}`);
}

afterEach(() => {
  vi.unstubAllGlobals();
  openWithHash("");
});

describe("ResultPage", () => {
  it("입장권이 없으면 챗봇에서 다시 열라고 안내한다", () => {
    openWithHash("");
    render(<ResultPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("결과를 열 수 없어요");
  });

  it("입장권을 헤더로 보내 결과를 받아 결론을 보여 준다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(caution)));
    vi.stubGlobal("fetch", fetchMock);
    openWithHash("#t=x7Kq");

    render(<ResultPage />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "주의가 필요한 문자예요",
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/result");
    expect(url).not.toContain("x7Kq");
    expect(init.headers).toEqual({ Authorization: "Bearer x7Kq" });
    expect(init.cache).toBe("no-store");
  });

  it("만료된 입장권이면 열람 시간이 지났다고 안내한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 410 })));
    openWithHash("#t=old");
    render(<ResultPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("열람 시간이 지났어요");
  });

  it("응답 모양이 어긋나면 불러오지 못했다고 안내한다", async () => {
    const broken = { ...caution, tone: "safe" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(broken))));
    openWithHash("#t=x7Kq");
    render(<ResultPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("결과를 불러오지 못했어요");
  });
});
