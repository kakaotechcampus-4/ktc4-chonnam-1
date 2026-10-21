import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UploadPage } from "./UploadPage";

function openWithHash(hash: string) {
  window.history.replaceState(null, "", `/upload/${hash}`);
}

function audio(name = "call.m4a", size = 1_000_000): File {
  const file = new File([], name);
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

/** 올리기 응답 하나와 상태 응답 차례를 정한 가짜 서버. 상태가 떨어지면 마지막 것을 되풀이한다. */
function fakeServer(
  upload: () => Promise<Response>,
  statuses: Array<() => Promise<Response>> = [],
) {
  const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === "POST") return upload();
    const next = statuses.length > 1 ? statuses.shift()! : statuses[0];
    return next();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const accepted = () => Promise.resolve(json({ case_id: "c-1" }, 202));
const status = (value: string) => () => Promise.resolve(json({ status: value }));

/** 미뤄 둔 promise와 타이머를 흘려보낸다. */
async function wait(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function chooseFile(file: File) {
  fireEvent.change(screen.getByLabelText("파일 고르기"), { target: { files: [file] } });
}

function sendButton() {
  return screen.getByRole("button", { name: /보내/ });
}

beforeEach(() => {
  vi.useFakeTimers();
  openWithHash("#t=x7Kq");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  openWithHash("");
});

describe("UploadPage", () => {
  it("입장권이 없으면 챗봇에서 다시 열라고 안내한다", () => {
    openWithHash("");
    render(<UploadPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("올리기 화면을 열 수 없어요");
    expect(screen.queryByLabelText("파일 고르기")).toBeNull();
  });

  it("파일을 고르기 전에는 보낼 수 없다", () => {
    render(<UploadPage />);
    expect(sendButton()).toBeDisabled();
  });

  it("못 받는 파일을 고르면 이유를 보여 주고 보내기를 막는다", () => {
    render(<UploadPage />);

    chooseFile(audio("call.amr"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "m4a, mp3, wav, webm 파일만 올릴 수 있어요.",
    );
    expect(sendButton()).toBeDisabled();

    chooseFile(audio("call.m4a", 30 * 1024 * 1024));
    expect(screen.getByRole("alert")).toHaveTextContent("25MB 이하 파일만 올릴 수 있어요.");

    chooseFile(audio("call.m4a"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(sendButton()).toBeEnabled();
  });

  it("보내기 위에 개인정보 안내를 보여 준다", () => {
    render(<UploadPage />);
    expect(
      screen.getByText(/보내기를 누르면 녹음을 분석하는 데 쓰는 것에 동의하는 거예요/),
    ).toBeVisible();
  });

  it("올리고 분석이 끝날 때까지 기다린 뒤 챗봇으로 돌려보낸다", async () => {
    const fetchMock = fakeServer(accepted, [status("running"), status("done")]);
    render(<UploadPage />);

    chooseFile(audio());
    fireEvent.click(sendButton());
    expect(sendButton()).toHaveTextContent("보내는 중…");
    expect(sendButton()).toBeDisabled();

    await wait();
    expect(screen.getByRole("status")).toHaveTextContent("녹음을 분석하고 있어요");
    expect(screen.getByRole("status")).toHaveTextContent("이 화면을 닫아도 분석은 계속돼요");

    await wait(2000);
    expect(screen.getByRole("status")).toHaveTextContent("녹음을 분석하고 있어요");

    await wait(2000);
    expect(screen.getByRole("status")).toHaveTextContent("분석이 끝났어요");
    expect(screen.getByRole("status")).toHaveTextContent(
      "챗봇으로 돌아가 '결과 확인'을 눌러 주세요.",
    );

    const urls = fetchMock.mock.calls.map(([url]) => url);
    expect(urls).toEqual(["/api/cases", "/api/cases/c-1", "/api/cases/c-1"]);
    expect(urls.join()).not.toContain("x7Kq");
  });

  it("챗봇이 질문할 차례(waiting_answer)여도 웹에서는 끝났다고 보여 준다", async () => {
    fakeServer(accepted, [status("waiting_answer")]);
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();
    await wait(2000);
    expect(screen.getByRole("status")).toHaveTextContent("분석이 끝났어요");
  });

  it("분석이 실패하면 챗봇에서 다시 올리게 한다", async () => {
    fakeServer(accepted, [status("failed")]);
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();
    await wait(2000);
    expect(screen.getByRole("alert")).toHaveTextContent("녹음을 분석하지 못했어요");
  });

  it("3분이 지나도 안 끝나면 묻기를 멈추고 챗봇에서 확인하게 한다", async () => {
    const fetchMock = fakeServer(accepted, [status("running")]);
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();

    await wait(3 * 60_000);
    expect(screen.getByRole("status")).toHaveTextContent("시간이 걸리고 있어요");

    const calls = fetchMock.mock.calls.length;
    await wait(60_000);
    expect(fetchMock.mock.calls.length).toBe(calls);
  });

  it("상태 묻기가 한 번 실패해도 다음 차례에 다시 묻는다", async () => {
    fakeServer(accepted, [() => Promise.reject(new TypeError("offline")), status("done")]);
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();
    await wait(2000);
    expect(screen.getByRole("status")).toHaveTextContent("녹음을 분석하고 있어요");
    await wait(2000);
    expect(screen.getByRole("status")).toHaveTextContent("분석이 끝났어요");
  });

  it("기다리는 중 입장권이 만료되면 분석은 계속되니 챗봇에서 확인하게 한다", async () => {
    fakeServer(accepted, [() => Promise.resolve(new Response("", { status: 410 }))]);
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();
    await wait(2000);
    expect(screen.getByRole("status")).toHaveTextContent(
      "잠시 뒤 챗봇에서 '결과 확인'을 눌러 주세요.",
    );
  });

  it("보내지 못하면 고른 파일을 그대로 두고 다시 보낼 수 있다", async () => {
    let attempt = 0;
    fakeServer(
      () => (++attempt === 1 ? Promise.reject(new TypeError("offline")) : accepted()),
      [status("running")],
    );
    render(<UploadPage />);
    chooseFile(audio("통화.m4a"));
    fireEvent.click(sendButton());
    await wait();

    expect(screen.getByRole("alert")).toHaveTextContent("보내지 못했어요");
    expect(screen.getByText(/통화\.m4a/)).toBeInTheDocument();
    expect(sendButton()).toBeEnabled();

    fireEvent.click(sendButton());
    await wait();
    expect(screen.getByRole("status")).toHaveTextContent("녹음을 분석하고 있어요");
  });

  it("서버가 파일을 거절하면 이유를 보여 주고 다른 파일을 고르게 한다", async () => {
    fakeServer(() => Promise.resolve(json({ error: "unsupported_type" }, 400)));
    render(<UploadPage />);
    chooseFile(audio("call.webm"));
    fireEvent.click(sendButton());
    await wait();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "m4a, mp3, wav, webm 파일만 올릴 수 있어요.",
    );
    expect(sendButton()).toBeDisabled();
  });

  it("이미 올린 입장권이면 결과 확인을 안내한다", async () => {
    fakeServer(() => Promise.resolve(new Response("", { status: 409 })));
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();
    expect(screen.getByRole("alert")).toHaveTextContent("이미 올린 녹음이 있어요");
  });

  it("입장권이 만료됐으면 챗봇에서 다시 열게 한다", async () => {
    fakeServer(() => Promise.resolve(new Response("", { status: 410 })));
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();
    expect(screen.getByRole("alert")).toHaveTextContent("올리기 화면을 열 수 없어요");
  });

  it("상태 조회가 아예 대답하지 않아도 3분이 되면 챗봇에서 확인하게 한다", async () => {
    // 끊으라는 신호도 무시하는 서버: 3분 시계가 대답과 상관없이 도는지 본다.
    fakeServer(accepted, [() => new Promise<Response>(() => {})]);
    render(<UploadPage />);
    chooseFile(audio());
    fireEvent.click(sendButton());
    await wait();

    await wait(3 * 60_000);
    expect(screen.getByRole("status")).toHaveTextContent("시간이 걸리고 있어요");
  });
});
