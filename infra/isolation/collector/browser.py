"""3단계 브라우저 수집 (설계: docs/superpowers/specs/2026-10-01-smishing-pipeline-design.md
"3단계 페이지 방문", 보안: docs/isolation-security.md §5.1·§5.2·§5.4·§5.9).

2단계(httpx) HTML 에 JS 의존 신호가 있을 때만 돈다. 판정은 하지 않고 본 것만 돌려준다.

브라우저는 하위 리소스·fetch·WebSocket 을 스스로 요청하므로 우리 HTTP 코드를 거치지 않는다.
그래서 경로마다 따로 막는다.

- 검사 프록시(egress_proxy.py): 브라우저의 **모든 연결**이 지나간다. 서버 리다이렉트를 따라간
  요청은 route 에 다시 나타나지 않으므로, 연결 단위 검사는 프록시가 맡는다
- context.route: 허용 목록 밖 요청을 보내기 전에 끊고 횟수를 센다. 이미지·폰트·미디어는 받지 않는다
- route_web_socket: WebSocket 은 허용 호스트여도 끊는다
- service_workers="block": 서비스 워커가 처리한 요청은 route 를 피하므로 아예 막는다
- accept_downloads=False: 다운로드는 주소만 기록하고 본문은 받지 않는다
- 새 탭·팝업은 바로 닫는다
- 최종 방어선은 호스트 방화벽이다 (컨테이너는 TEST_PAGE_IP:443 으로만 나갈 수 있다)
"""

import asyncio
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from collector.egress_proxy import EgressProxy
from scanner.fetch import DEFAULT_HEADERS, HTML_LIMIT_BYTES, extract_title
from scanner.models import BrowserResult, CollectResult

log = logging.getLogger("collector.browser")

BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "media"})  # 속도·메모리 (설계 §t3.medium)
FORM_SELECTOR = "form, input[type=password], input[type=tel]"
MAX_NAVIGATIONS = 10
MAX_DOWNLOADS = 5
MAX_URL_CHARS = 2048
SNAPSHOT_RESERVE_SECONDS = 1.0  # 마감 뒤에도 그때까지 본 화면을 가져올 몫
CLOSE_TIMEOUT_SECONDS = 1.5
BROWSER_ENV_KEYS = ("PATH", "HOME", "LANG", "PLAYWRIGHT_BROWSERS_PATH")
MOBILE_VIEWPORT = {"width": 412, "height": 915}

# ---------------------------------------------------------------- 3단계로 넘길지

MIN_VISIBLE_TEXT_CHARS = 200
STAGE2_OK_FAILURES = frozenset({"html_truncated"})
_SCRIPT_BODY_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_NON_TEXT_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_REDIRECT_HINT_RE = re.compile(r"\blocation(?:\.href)?\s*=(?!=)|\blocation\.(?:replace|assign)\s*\(")
_META_REFRESH_RE = re.compile(r"<meta[^>]+http-equiv\s*=\s*[\"']?refresh", re.IGNORECASE)


def visible_text_length(html: str) -> int:
    text = _TAG_RE.sub(" ", _NON_TEXT_RE.sub(" ", html))
    return len(re.sub(r"\s+", "", text))


def browser_trigger(result: CollectResult) -> str | None:
    """JS 의존 신호가 있으면 그 이름을, 없으면 None 을 돌려준다.

    수집 실패·다운로드·바이너리 응답은 3단계로 넘기지 않는다 (설계 문서와 같은 기준).
    """
    if set(result.failures) - STAGE2_OK_FAILURES or not result.html:
        return None
    if result.status_code in (403, 404):
        return "status_403_404"
    if _META_REFRESH_RE.search(result.html) or any(
        _REDIRECT_HINT_RE.search(body) for body in _SCRIPT_BODY_RE.findall(result.html)
    ):
        return "script_redirect"
    if "<script" in result.html.lower() and visible_text_length(result.html) < MIN_VISIBLE_TEXT_CHARS:
        return "script_only_page"
    return None


# ---------------------------------------------------------------- 렌더링


def _truncate(html: str, limit: int = HTML_LIMIT_BYTES) -> tuple[str, bool]:
    encoded = html.encode("utf-8")
    if len(encoded) <= limit:
        return html, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


@dataclass
class _Seen:
    navigations: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    blocked: int = 0
    blocked_navigation: bool = False
    page: object = None  # 이 작업의 메인 페이지. 팝업의 이동과 구분한다
    # 다운로드 시작·메인 페이지 이동 차단처럼 더 기다려도 볼 것이 없는 일이 생기면 켠다.
    # 이런 경우 goto 가 끝나지 않고 마감까지 붙잡혀 있는 것을 로컬에서 확인했다.
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    failures: list[str] = field(default_factory=list)
    crashed: bool = False

    def fail(self, code: str) -> None:
        if code not in self.failures:
            self.failures.append(code)


def _is_main_navigation(request, main_page) -> bool:
    try:
        frame = request.frame
        return request.is_navigation_request() and frame.parent_frame is None and frame.page == main_page
    except PlaywrightError:  # 프레임이 이미 사라졌거나 서비스 워커 요청
        return False


def _classify(exc: BaseException, proxy_blocked: int) -> str:
    message = str(exc)
    if isinstance(exc, (TimeoutError, PlaywrightTimeoutError)):
        return "browser_timeout"
    # route 가 막으면 ERR_BLOCKED_BY_CLIENT, 리다이렉트 끝을 프록시가 막으면 다른 오류로 끝난다.
    if "ERR_BLOCKED_BY_CLIENT" in message or proxy_blocked:
        return "browser_blocked_navigation"
    if "crash" in message.lower():
        return "browser_crashed"
    return "browser_navigation_failed"


class BrowserRenderer:
    """브라우저 하나를 띄워 두고 요청마다 새 컨텍스트로 연다 (웜 풀 1개).

    한 번에 한 작업만 한다. `restart_every` 페이지마다, 또는 크래시·정리 실패 뒤에는
    브라우저를 다시 띄운다. 쿠키·캐시는 컨텍스트를 닫을 때 함께 사라진다.
    """

    def __init__(
        self,
        url_allowed: Callable[[str], bool],
        proxy: EgressProxy,
        *,
        restart_every: int = 50,
        sandbox: bool = True,
        ignore_https_errors: bool = False,
    ) -> None:
        self._url_allowed = url_allowed
        self._proxy = proxy
        self._restart_every = restart_every
        self._sandbox = sandbox  # 테스트 외에는 끄지 않는다. 운영 진입점은 항상 True 로 만든다
        self._ignore_https_errors = ignore_https_errors
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._served = 0
        self._broken = False
        self._tasks: set[asyncio.Task] = set()

    # -- 수명 관리

    def _healthy(self) -> bool:
        return (
            self._browser is not None
            and self._browser.is_connected()
            and not self._broken
            and self._served < self._restart_every
        )

    async def _ensure(self):
        if self._healthy():
            return self._browser
        await self._shutdown()
        # 브라우저에는 최소 환경변수만 넘긴다. 기본값은 수집기 프로세스의 환경 전체다 (§5.1).
        env = {key: os.environ[key] for key in BROWSER_ENV_KEYS if key in os.environ}
        await self._proxy.start()
        self._pw = await async_playwright().start()
        # 기본 headless shell 을 쓴다. channel="chromium"(새 headless)은 구글 서버로 배경 통신을
        # 시도해 차단 횟수에 섞인다 (로컬 비교로 확인).
        self._browser = await self._pw.chromium.launch(
            chromium_sandbox=self._sandbox,
            proxy={"server": self._proxy.url},
            env=env,
            handle_sigint=False,
            handle_sigterm=False,
            handle_sighup=False,
        )
        self._served = 0
        self._broken = False
        log.info("event=browser_started version=%s", self._browser.version)
        return self._browser

    async def _shutdown(self) -> None:
        browser, pw = self._browser, self._pw
        self._browser = self._pw = None
        for closer in (browser.close if browser else None, pw.stop if pw else None):
            if closer is None:
                continue
            try:
                await asyncio.wait_for(closer(), 3.0)
            except Exception:  # noqa: BLE001 — 정리 실패는 다음 기동을 막지 않는다
                log.warning("event=browser_shutdown_failed")

    async def start(self) -> None:
        """서버 기동 시 미리 띄운다. 실패하면 첫 요청에서 다시 시도한다."""
        async with self._lock:
            try:
                await self._ensure()
            except Exception:  # noqa: BLE001
                log.exception("event=browser_start_failed")

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown()
            await self._proxy.close()

    def mark_broken(self) -> None:
        self._broken = True

    def _keep(self, task: asyncio.Future) -> None:
        """끝나지 않은 Playwright 호출을 버리지 않고 들고 있다가, 끝나면 오류를 거둔다.

        Playwright 호출을 취소하면 내부 Future 가 남아 나중에 "never retrieved" 오류를 낸다.
        그래서 시간이 넘으면 취소하지 않고 기다리기만 멈춘다. 컨텍스트를 닫으면 함께 끝난다.
        """
        self._tasks.add(task)

        def reap(done: asyncio.Future) -> None:
            self._tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(reap)

    async def _bounded(self, awaitable, timeout: float):
        task = asyncio.ensure_future(awaitable)
        done, _ = await asyncio.wait({task}, timeout=max(timeout, 0.01))
        if task in done:
            return task.result()
        self._keep(task)
        raise TimeoutError

    def _restart_in_background(self) -> None:
        async def restart() -> None:
            async with self._lock:
                try:
                    await self._ensure()
                except Exception:  # noqa: BLE001
                    log.exception("event=browser_restart_failed")

        task = asyncio.create_task(restart())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- 한 페이지

    async def render(self, url: str, *, trigger: str, timeout: float) -> BrowserResult:
        start = time.monotonic()
        deadline = start + timeout
        seen = _Seen()
        html = ""
        final_url: str | None = None

        def remaining(reserve: float = 0.0) -> float:
            return max(deadline - time.monotonic() - reserve, 0.05)

        try:
            # 대기열(락)과 기동 시간도 6초 예산에 넣는다 (설계 "대기열 포함").
            await asyncio.wait_for(self._lock.acquire(), remaining(SNAPSHOT_RESERVE_SECONDS))
        except TimeoutError:
            seen.fail("browser_timeout")
            return self._result(trigger, seen, start, final_url, html)

        context = page = None
        proxy_blocked_before = self._proxy.blocked
        try:
            browser = await asyncio.wait_for(self._ensure(), remaining(SNAPSHOT_RESERVE_SECONDS))
            proxy_blocked_before = self._proxy.blocked
            context = await self._new_context(browser, seen)
            page = await context.new_page()
            seen.page = page
            self._watch(context, page, seen)
            budget = remaining(SNAPSHOT_RESERVE_SECONDS)
            try:
                await self._bounded(self._visit(page, url, budget, seen), budget + 0.5)
            except (TimeoutError, PlaywrightError) as exc:
                seen.fail(_classify(exc, self._proxy.blocked - proxy_blocked_before))
            if seen.blocked_navigation:
                seen.fail("browser_blocked_navigation")
            final_url, html = await self._snapshot(page, seen)
        except TimeoutError:
            seen.fail("browser_timeout")
        except Exception:  # noqa: BLE001 — 브라우저 오류가 API 를 죽이지 않게
            log.exception("event=browser_error")
            seen.fail("browser_error")
            self._broken = True
        finally:
            self._served += 1
            seen.blocked += self._proxy.blocked - proxy_blocked_before
            if context is not None:
                try:
                    await self._bounded(context.close(), CLOSE_TIMEOUT_SECONDS)
                except Exception:  # noqa: BLE001
                    self._broken = True
            if seen.crashed:
                self._broken = True
            needs_restart = not self._healthy()
            self._lock.release()
            if needs_restart:
                self._restart_in_background()

        return self._result(trigger, seen, start, final_url, html)

    async def _new_context(self, browser, seen: _Seen):
        context = await browser.new_context(
            accept_downloads=False,
            service_workers="block",
            ignore_https_errors=self._ignore_https_errors,
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="ko-KR",
            viewport=MOBILE_VIEWPORT,
            is_mobile=True,
            has_touch=True,
        )

        async def on_route(route) -> None:
            request = route.request
            if not self._url_allowed(request.url):
                seen.blocked += 1
                if _is_main_navigation(request, seen.page):
                    # 메인 프레임 이동을 끊으면 크롬이 오류 페이지로 바꿔 버린다. 204 를 주면
                    # 이동이 취소되고 이동을 시도한 페이지가 그대로 남아 그 화면을 기록할 수 있다.
                    seen.blocked_navigation = True
                    seen.stop.set()
                    await route.fulfill(status=204)
                else:
                    await route.abort("blockedbyclient")
            elif request.resource_type in BLOCKED_RESOURCE_TYPES:
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        async def on_websocket(ws) -> None:
            seen.blocked += 1
            await ws.close(code=1008, reason="blocked")

        await context.route("**/*", on_route)
        await context.route_web_socket(re.compile(r".*"), on_websocket)
        return context

    def _watch(self, context, page, seen: _Seen) -> None:
        def on_navigated(frame) -> None:
            if frame != page.main_frame or not frame.url.startswith(("http://", "https://")):
                return  # 크롬 오류 페이지(chrome-error://)·about:blank 는 이동으로 치지 않는다
            if len(seen.navigations) < MAX_NAVIGATIONS:
                seen.navigations.append(frame.url[:MAX_URL_CHARS])

        def on_download(download) -> None:
            if len(seen.downloads) < MAX_DOWNLOADS:
                seen.downloads.append(download.url[:MAX_URL_CHARS])
            seen.stop.set()

        def on_crash(_page) -> None:
            seen.crashed = True
            seen.fail("browser_crashed")

        def on_new_page(other) -> None:
            if other == page:
                return
            seen.blocked += 1
            task = asyncio.create_task(other.close())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        page.on("framenavigated", on_navigated)
        page.on("download", on_download)
        page.on("crash", on_crash)
        context.on("page", on_new_page)

    async def _visit(self, page, url: str, budget: float, seen: _Seen) -> None:
        # 마감은 Playwright 자체 timeout 으로 건다. asyncio 로 취소하면 내부 Future 가 남는다.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget

        def ms() -> int:
            return max(int((deadline - loop.time()) * 1000), 1)

        # 페이지가 읽히는 중에 다운로드·막힌 이동이 생기면 goto 가 끝나지 않을 수 있어 경주시킨다.
        nav = asyncio.ensure_future(page.goto(url, wait_until="domcontentloaded", timeout=ms()))
        stop = asyncio.ensure_future(seen.stop.wait())
        await asyncio.wait({nav, stop}, return_when=asyncio.FIRST_COMPLETED)
        stop.cancel()  # 우리 Event 대기라 취소해도 Playwright 에 남는 것이 없다
        if not nav.done():
            self._keep(nav)
            return
        try:
            nav.result()
        except PlaywrightError as exc:
            # 이동한 주소가 파일이면 goto 가 이 오류로 끝난다. 다운로드 이벤트로 이미 기록했다.
            if "Download is starting" in str(exc):
                return
            raise
        if seen.stop.is_set():
            return
        # 폼이 나타나거나, 네트워크가 잠잠해지거나, 위의 멈출 일이 생기면 끝낸다 (설계 §t3.medium).
        # 진 쪽은 취소하지 않고 기다리기만 멈춘다.
        idle = asyncio.ensure_future(page.wait_for_load_state("networkidle", timeout=ms()))
        form = asyncio.ensure_future(page.wait_for_selector(FORM_SELECTOR, state="attached", timeout=ms()))
        stop = asyncio.ensure_future(seen.stop.wait())
        await asyncio.wait({idle, form, stop}, return_when=asyncio.FIRST_COMPLETED)
        stop.cancel()
        for task in (idle, form):
            self._keep(task)

    async def _snapshot(self, page, seen: _Seen) -> tuple[str | None, str]:
        """마감이 지났어도 그때까지 본 화면을 가져온다. 렌더러가 멈췄으면 빈 값으로 둔다.

        이동이 실패해 크롬 오류 페이지가 떠 있으면 그 HTML 은 상대 페이지가 아니므로 버리고,
        최종 주소는 마지막으로 실제 열린 주소로 둔다.
        """
        if not page.url.startswith(("http://", "https://")):
            return (seen.navigations[-1] if seen.navigations else None), ""
        final_url = page.url[:MAX_URL_CHARS]
        for _ in range(2):
            try:
                return final_url, await self._bounded(page.content(), 0.8)
            except TimeoutError:
                break
            except PlaywrightError:
                # 이동 중이면 실행 컨텍스트가 사라져 실패한다. 잠깐 뒤 한 번 더.
                await asyncio.sleep(0.1)
        seen.fail("browser_snapshot_failed")
        return final_url, ""

    @staticmethod
    def _result(trigger, seen: _Seen, start: float, final_url, html: str) -> BrowserResult:
        html, truncated = _truncate(html)
        if truncated:
            seen.fail("html_truncated")
        return BrowserResult(
            trigger=trigger,
            final_url=final_url,
            navigation_chain=tuple(seen.navigations),
            html=html,
            title=extract_title(html) if html else None,
            downloads=tuple(seen.downloads),
            blocked_requests=seen.blocked,
            elapsed_ms=int((time.monotonic() - start) * 1000),
            failures=tuple(seen.failures),
        )
