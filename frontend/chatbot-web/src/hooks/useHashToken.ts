import { useEffect, useState } from "react";
import { readToken } from "../lib/token";

/** 주소 `#t=`의 입장권. `#` 뒤가 바뀌면 다시 읽는다. 업로드 페이지도 같이 쓴다. */
export function useHashToken(): string | null {
  const [token, setToken] = useState(readToken);

  useEffect(() => {
    const onHashChange = () => setToken(readToken());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return token;
}
