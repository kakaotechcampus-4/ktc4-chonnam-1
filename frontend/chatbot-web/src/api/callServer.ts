import type { z } from "zod/mini";

/** 요청 결과. 오류는 던지지 않고 돌려준다. 받는 쪽은 타입만 보고 어떤 오류가 올지 안다. */
export type Result<T, E> = { ok: true; data: T } | { ok: false; error: E };

interface CallOptions<T, E extends string, S extends string, F extends string> {
  method?: "GET" | "POST";
  body?: BodyInit;
  /** 성공(2xx) 본문의 모양 */
  ok: z.ZodMiniType<T>;
  /** 상태 코드별 오류 이름 */
  errors?: Partial<Record<number, E>>;
  /** 본문을 읽어야 오류 이름을 아는 상태 코드와, 이름을 꺼낼 모양. 모양이 어긋나면 fallback */
  errorBodies?: Partial<Record<number, z.ZodMiniType<S>>>;
  /** 인터넷 끊김, 그 밖의 서버 오류, 모양이 어긋난 본문 */
  fallback: F;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/**
 * 입장권을 헤더에 넣어 BE에 요청하고, 답장을 약속한 모양으로 확인한다(ADR 0006).
 * 입장권은 주소에 넣지 않고, 브라우저가 답장을 저장하지 않게 한다. 401·410은 늘 만료다.
 */
export async function callServer<
  T,
  E extends string = never,
  S extends string = never,
  F extends string = never,
>(
  token: string,
  path: string,
  { method, body, ok, errors = {}, errorBodies = {}, fallback }: CallOptions<T, E, S, F>,
): Promise<Result<T, "expired" | E | S | F>> {
  const fail = (error: "expired" | E | S | F) => ({ ok: false, error }) as const;

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      body,
      cache: "no-store",
      credentials: "omit",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    return fail(fallback);
  }
  if (response.status === 401 || response.status === 410) return fail("expired");

  const named = errors[response.status];
  if (named) return fail(named);
  const json = await readJson(response);
  const errorBody = errorBodies[response.status];
  if (errorBody) {
    const parsed = errorBody.safeParse(json);
    return fail(parsed.success ? parsed.data : fallback);
  }
  if (!response.ok) return fail(fallback);

  const parsed = ok.safeParse(json);
  return parsed.success ? { ok: true, data: parsed.data } : fail(fallback);
}
