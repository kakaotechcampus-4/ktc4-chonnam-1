// 입장권은 주소의 `#` 뒤에 둔다. `#` 뒤는 서버로 보내지 않아 기록에 남지 않는다 (ADR 0006).
// 업로드 페이지와 결과 페이지가 같이 쓴다.

/** `#t=x7Kq` → `x7Kq`. 없거나 비었으면 null. */
export function parseToken(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("t");
  return token && token.trim() ? token.trim() : null;
}

export function readToken(): string | null {
  return parseToken(window.location.hash);
}
