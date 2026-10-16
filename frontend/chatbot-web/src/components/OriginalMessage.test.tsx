import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OriginalMessage } from "./OriginalMessage";
import type { Highlight } from "../types";

const claim: Highlight = {
  quote: "청소행정과",
  kind: "claim",
  label: "내세운 기관",
  note: "주장한 기관이에요",
};

describe("OriginalMessage: 받은 문자를 안전하게 보여 준다", () => {
  it("원문에 태그가 있어도 실행하지 않고 글자로만 보여 준다", () => {
    const { container } = render(
      <OriginalMessage
        messageText={'<script>alert(1)</script><img src=x onerror="x()">'}
        highlights={[]}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
  });

  it("원문 속 주소를 눌리는 링크로 만들지 않고 무해화해서 보여 준다", () => {
    const { container } = render(
      <OriginalMessage messageText="아래 링크 https://clean-notice.example/b" highlights={[]} />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText(/hxxps:\/\/clean-notice\[\.\]example\/b/)).toBeInTheDocument();
    expect(container.textContent).not.toContain("https://clean-notice.example");
  });

  it("원문에 있는 인용은 칠하고 번호와 설명을 붙인다", () => {
    const { container } = render(
      <OriginalMessage messageText="[청소행정과] 신고가 접수되었습니다" highlights={[claim]} />,
    );
    expect(container.querySelector("mark")).toHaveTextContent("청소행정과1");
    expect(screen.getByText("주장한 기관이에요")).toBeInTheDocument();
  });

  it("원문에 없는 인용은 칠하지도 설명하지도 않는다", () => {
    const { container } = render(
      <OriginalMessage messageText="평범한 문자예요" highlights={[claim]} />,
    );
    expect(container.querySelector("mark")).toBeNull();
    expect(screen.queryByText("주장한 기관이에요")).toBeNull();
  });
});
