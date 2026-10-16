import { describe, expect, it } from "vitest";
import { defang } from "./defang";

describe("defang: 원문 속 주소를 눌리지 않게 바꾼다", () => {
  it("https 주소의 스킴과 점을 바꾼다", () => {
    expect(defang("https://clean-notice.example/b")).toBe("hxxps://clean-notice[.]example/b");
  });

  it("http 주소도 바꾼다", () => {
    expect(defang("http://a.example")).toBe("hxxp://a[.]example");
  });

  it("대문자 스킴도 소문자 hxxps로 바꾼다", () => {
    expect(defang("HTTPS://A.EXAMPLE/x")).toBe("hxxps://A[.]EXAMPLE/x");
  });

  it("스킴이 없는 주소도 점을 바꾼다", () => {
    expect(defang("www.cj-delivery-check.example/a")).toBe("www[.]cj-delivery-check[.]example/a");
  });

  it("경로의 점은 그대로 두고 호스트의 점만 바꾼다", () => {
    expect(defang("https://a.example/file.apk")).toBe("hxxps://a[.]example/file.apk");
  });

  it("포트가 있어도 호스트의 점을 바꾼다", () => {
    expect(defang("https://a.example:8443/x")).toBe("hxxps://a[.]example:8443/x");
  });

  it("한글 문장 사이의 주소도 바꾼다", () => {
    expect(defang("확인해 보세요 https://example.com 지금")).toBe(
      "확인해 보세요 hxxps://example[.]com 지금",
    );
  });

  it("한 글에 주소가 여럿이면 모두 바꾼다", () => {
    expect(defang("a.example 그리고 b.example")).toBe("a[.]example 그리고 b[.]example");
  });

  it("바꾼 결과에는 눌리는 주소 모양(http://, 점으로 이어진 호스트)이 남지 않는다", () => {
    const out = defang("https://evil.example/login?next=https://bank.example");
    expect(out).not.toMatch(/https?:\/\//i);
    expect(out).not.toMatch(/[a-z0-9-]+\.[a-z]{2,}\//i);
  });

  it("IP 주소도 스킴과 점을 바꾼다", () => {
    expect(defang("http://1.2.3.4/x")).toBe("hxxp://1[.]2[.]3[.]4/x");
  });

  it("스킴이 없는 IP 주소와 포트도 점을 바꾼다", () => {
    expect(defang("접속 1.2.3.4:8080/login 하세요")).toBe("접속 1[.]2[.]3[.]4:8080/login 하세요");
  });

  it("경로 속에 든 IP 주소도 바꾼다", () => {
    expect(defang("https://a.example/r?next=http://1.2.3.4/x")).toBe(
      "hxxps://a[.]example/r?next=hxxp://1[.]2[.]3[.]4/x",
    );
  });

  it("주소가 아닌 글과 숫자는 건드리지 않는다", () => {
    const text = "과태료 3.5만 원, 고객센터 1588-1300으로 연락하세요.";
    expect(defang(text)).toBe(text);
  });
});
