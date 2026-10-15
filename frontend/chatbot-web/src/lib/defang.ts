// 무해화: 원문 속 주소가 눌리는 링크가 되지 않게 바꾼다 (ADR 0006).
// 사용자 브라우저가 의심 페이지에 닿지 않게 하는 격리 환경 원칙을 웹까지 잇는다.

const URL_PATTERN = /\b(?:https?:\/\/)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:\/[^\s]*)?/gi;

/** `https://a.example/b` → `hxxps://a[.]example/b`. 경로 안에 든 주소(`?next=https://…`)도 바꾼다. */
export function defang(text: string): string {
  return text.replace(URL_PATTERN, (match) => {
    const schemeEnd = match.indexOf("://");
    const scheme = schemeEnd >= 0 ? match.slice(0, schemeEnd).toLowerCase() : "";
    const rest = schemeEnd >= 0 ? match.slice(schemeEnd + 3) : match;
    const slash = rest.indexOf("/");
    const host = slash >= 0 ? rest.slice(0, slash) : rest;
    const path = slash >= 0 ? rest.slice(slash) : "";
    const safeScheme = scheme ? `${scheme.replace("http", "hxxp")}://` : "";
    return `${safeScheme}${host.replace(/\./g, "[.]")}${defangPath(path)}`;
  });
}

/** 경로 안의 주소만 다시 바꾼다. 경로 자체의 점(`file.apk`)은 두되, 들어 있는 주소는 바꾼다. */
function defangPath(path: string): string {
  return path.replace(/(?:https?:\/\/)(?:[a-z0-9-]+\.)+[a-z]{2,}[^\s&]*/gi, (inner) =>
    defang(inner),
  );
}
