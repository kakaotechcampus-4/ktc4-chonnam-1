"""Execute-free inspection of HTML supplied by the isolated page collector."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re

from ai.types import EnvDoubt, FailureCode


MAX_HTML_BYTES = 131_072
MAX_ELEMENTS = 2_000
MAX_PAGE_TEXT_CHARS = 16_000
IGNORED_TEXT_TAGS = frozenset({"script", "style", "template", "noscript"})

_ALLOWED_ATTRIBUTES = frozenset(
    {
        "type",
        "name",
        "autocomplete",
        "href",
        "action",
        "aria-label",
        "hidden",
        "style",
    }
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_INPUT_TAGS = frozenset({"input", "textarea", "select"})
_INPUT_CONTAINERS = frozenset(
    {"fieldset", "div", "section", "main", "article", "aside"}
)
_DOCUMENT_EXTENSIONS = re.compile(
    r"\.(?:pdf|docx?|xlsx?|pptx?|txt|rtf|jpe?g|png|gif|webp)(?:[?#]|$)", re.I
)
_APP_EXTENSIONS = re.compile(r"\.(?:apk|aab|ipa)(?:[?#]|$)", re.I)


@dataclass(frozen=True)
class PageElement:
    element_id: str
    doubt: EnvDoubt
    evidence: str
    start: int
    tag: str
    attributes: dict[str, str]
    text: str


@dataclass(frozen=True)
class PageInspection:
    text: str
    elements: tuple[PageElement, ...]
    failure: FailureCode | None


@dataclass
class _Node:
    sequence: int
    tag: str
    start: int
    start_tag_end: int
    raw_attributes: dict[str, str]
    attributes: dict[str, str]
    parent: _Node | None
    ignored: bool
    content: list[str | _Node] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)
    end: int | None = None
    incomplete: bool = False


class _InspectionLimit(Exception):
    pass


class _Collector(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.nodes: list[_Node] = []
        self.stack: list[_Node] = []
        self.page_chunks: list[str] = []
        self.text_chars = 0
        self.limit_reached = False
        self.malformed_important = False
        self._line_offsets = [0]
        for line in source.splitlines(keepends=True):
            self._line_offsets.append(self._line_offsets[-1] + len(line))

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def _tag_end(self, start: int) -> int:
        closing = self.source.find(">", start)
        return len(self.source) if closing < 0 else closing + 1

    def _new_node(
        self, tag: str, attrs: list[tuple[str, str | None]], *, closes_itself: bool
    ) -> None:
        if len(self.nodes) >= MAX_ELEMENTS:
            self.limit_reached = True
            raise _InspectionLimit

        start = self._offset()
        start_tag_text = self.get_starttag_text()
        start_tag_end = (
            start + len(start_tag_text)
            if start_tag_text is not None
            else self._tag_end(start)
        )
        raw_attributes: dict[str, str] = {}
        for name, value in attrs:
            lowered = name.lower()
            if lowered not in raw_attributes:
                raw_attributes[lowered] = "" if value is None else value
        safe_attributes = {
            name: value
            for name, value in raw_attributes.items()
            if name in _ALLOWED_ATTRIBUTES
        }
        parent = self.stack[-1] if self.stack else None
        ignored = tag in IGNORED_TEXT_TAGS or bool(parent and parent.ignored)
        node = _Node(
            sequence=len(self.nodes) + 1,
            tag=tag,
            start=start,
            start_tag_end=start_tag_end,
            raw_attributes=raw_attributes,
            attributes=safe_attributes,
            parent=parent,
            ignored=ignored,
        )
        self.nodes.append(node)
        if parent is not None:
            parent.children.append(node)
            parent.content.append(node)
        if closes_itself or tag in _VOID_TAGS:
            node.end = node.start_tag_end
        else:
            self.stack.append(node)

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._new_node(tag.lower(), attrs, closes_itself=False)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._new_node(tag.lower(), attrs, closes_itself=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        match = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index].tag == tag
            ),
            None,
        )
        if match is None:
            if tag == "form" and not (self.stack and self.stack[-1].ignored):
                self.malformed_important = True
            return

        end = self._tag_end(self._offset())
        intervening = self.stack[match + 1 :]
        matched = self.stack[match]
        for node in intervening:
            node.incomplete = True
            node.end = end
        if matched.tag == "form" and intervening and not matched.ignored:
            matched.incomplete = True
            self.malformed_important = True
        matched.end = end
        del self.stack[match:]

    def handle_data(self, data: str) -> None:
        if not data or (self.stack and self.stack[-1].ignored):
            return
        remaining = MAX_PAGE_TEXT_CHARS - self.text_chars
        if remaining <= 0:
            self.limit_reached = True
            raise _InspectionLimit
        kept = data[:remaining]
        self.page_chunks.append(kept)
        if self.stack:
            self.stack[-1].content.append(kept)
        self.text_chars += len(kept)
        if len(kept) != len(data):
            self.limit_reached = True
            raise _InspectionLimit

    def finish(self) -> None:
        for node in self.stack:
            node.end = len(self.source)
            node.incomplete = True
            if node.tag == "form" and not node.ignored:
                self.malformed_important = True


def _normalize_text(parts: list[str] | tuple[str, ...] | str) -> str:
    if isinstance(parts, str):
        value = parts
    else:
        value = " ".join(parts)
    return " ".join(value.split())


def _node_text(node: _Node) -> str:
    parts: list[str] = []
    for item in node.content:
        if isinstance(item, str):
            parts.append(item)
        elif not item.ignored:
            parts.append(_node_text(item))
    return _normalize_text(parts)


def _descendants(node: _Node) -> list[_Node]:
    found: list[_Node] = []
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        if child.ignored:
            continue
        found.append(child)
        pending.extend(reversed(child.children))
    return found


def _has_ancestor(node: _Node, tag: str) -> bool:
    current = node.parent
    while current is not None:
        if current.tag == tag:
            return True
        current = current.parent
    return False


def _field_context(field_node: _Node, root: _Node, labels: list[_Node]) -> str:
    pieces = [
        field_node.raw_attributes.get(name, "")
        for name in ("type", "name", "autocomplete", "aria-label")
    ]
    current = field_node.parent
    while current is not None and current is not root.parent:
        if current.tag == "label":
            pieces.append(_node_text(current))
            break
        current = current.parent
    field_id = field_node.raw_attributes.get("id")
    if field_id:
        pieces.extend(
            _node_text(label)
            for label in labels
            if label.raw_attributes.get("for") == field_id
        )
    return _normalize_text(pieces).casefold()


def _matches_named_field(context: str, names: tuple[str, ...]) -> bool:
    return any(
        name in context
        if not name.isascii()
        else re.search(rf"(?:^|[_\-\s]){re.escape(name)}(?:$|[_\-\s])", context)
        is not None
        for name in names
    )


def _is_payment_request(text: str) -> bool:
    compact = _normalize_text(text).casefold()
    explicit = re.search(
        r"결제\s*(?:하기|진행|요청|하세요|해\s*주세요|버튼|$)|"
        r"(?:pay|payment)\s*(?:now|here|button)?\b",
        compact,
    )
    if explicit is None:
        return False
    completed = re.search(r"결제\s*(?:완료|성공|되었습니다|됐습니다)", compact)
    return completed is None or bool(re.search(r"결제\s*(?:하기|진행|요청|하세요)", compact))


def _classify_input_root(root: _Node, labels: list[_Node]) -> tuple[EnvDoubt, list[_Node]] | None:
    fields = [
        node
        for node in [root, *_descendants(root)]
        if node.tag in _INPUT_TAGS and not node.ignored
    ]
    if not fields:
        return None

    root_text = _node_text(root).casefold()
    candidates: list[tuple[int, int, EnvDoubt, _Node]] = []
    priority = {
        EnvDoubt.LOGIN_FORM: 0,
        EnvDoubt.ADDRESS_FORM: 1,
        EnvDoubt.PARCEL_WIDGET: 2,
        EnvDoubt.PERSONAL_FORM: 3,
    }
    for input_node in fields:
        context = _field_context(input_node, root, labels)
        input_type = input_node.raw_attributes.get("type", "").casefold()
        autocomplete = input_node.raw_attributes.get("autocomplete", "").casefold()
        is_login = (
            input_type == "password"
            or autocomplete == "one-time-code"
            or _matches_named_field(context, ("비밀번호", "인증번호", "otp", "password"))
            or (
                _matches_named_field(
                    context, ("계정", "아이디", "사용자명", "account", "login", "user")
                )
                and any(word in root_text for word in ("로그인", "인증", "본인 확인", "sign in"))
            )
        )
        is_address = autocomplete in {
            "street-address",
            "address-line1",
            "address-line2",
            "address-line3",
            "postal-code",
            "shipping street-address",
            "billing street-address",
        } or _matches_named_field(
            context,
            ("주소", "우편번호", "address", "postal", "postcode", "zipcode"),
        )
        is_tracking = _matches_named_field(
            context,
            ("운송장", "송장", "배송조회", "tracking", "waybill", "invoice"),
        )
        is_personal = autocomplete in {
            "name",
            "given-name",
            "family-name",
            "tel",
        } or _matches_named_field(
            context,
            ("이름", "성명", "전화번호", "휴대폰", "연락처", "name", "phone", "tel"),
        )
        for matches, doubt in (
            (is_login, EnvDoubt.LOGIN_FORM),
            (is_address, EnvDoubt.ADDRESS_FORM),
            (is_tracking, EnvDoubt.PARCEL_WIDGET),
            (is_personal, EnvDoubt.PERSONAL_FORM),
        ):
            if matches:
                candidates.append((input_node.start, priority[doubt], doubt, input_node))

    specific = {
        candidate[2]
        for candidate in candidates
        if candidate[2] in {EnvDoubt.LOGIN_FORM, EnvDoubt.ADDRESS_FORM}
    }
    if specific:
        candidates = [
            candidate
            for candidate in candidates
            if candidate[2] is not EnvDoubt.PERSONAL_FORM
        ]
    if candidates:
        _, _, doubt, _ = min(candidates)
        return doubt, fields
    if _is_payment_request(root_text):
        return EnvDoubt.PAYMENT, fields
    return None


def _nearest_input_root(node: _Node) -> _Node:
    current = node.parent
    while current is not None:
        if current.tag in _INPUT_CONTAINERS:
            return current
        current = current.parent
    return node


def _combined_attributes(root: _Node, fields: list[_Node] = ()) -> dict[str, str]:
    combined = dict(root.attributes)
    for input_node in fields:
        for name, value in input_node.attributes.items():
            combined.setdefault(name, value)
    return combined


def _element(source: str, root: _Node, doubt: EnvDoubt, fields: list[_Node] = ()) -> PageElement:
    end = root.end if root.end is not None else len(source)
    return PageElement(
        element_id=f"element-{root.sequence:04d}",
        doubt=doubt,
        evidence=source[root.start:end],
        start=root.start,
        tag=root.tag,
        attributes=_combined_attributes(root, fields),
        text=_node_text(root),
    )


def _link_doubt(node: _Node) -> EnvDoubt | None:
    href = node.raw_attributes.get("href", "")
    if not href:
        return None
    context = _normalize_text(
        [_node_text(node), node.raw_attributes.get("aria-label", ""), href]
    ).casefold()
    app_words = any(word in context for word in ("앱", "어플", "app", "설치"))
    download_words = any(word in context for word in ("다운로드", "download", "설치"))
    if _APP_EXTENSIONS.search(href) or (app_words and download_words):
        return EnvDoubt.APP_LINK
    document_words = any(
        word in context
        for word in ("사진", "문서", "파일", "첨부", "고지서", "명세서", "photo", "document")
    )
    if _DOCUMENT_EXTENSIONS.search(href) or document_words:
        return EnvDoubt.DOCUMENT_VIEW
    return None


def _is_document_control(node: _Node) -> bool:
    context = _normalize_text(
        [
            _node_text(node),
            node.raw_attributes.get("aria-label", ""),
            node.raw_attributes.get("alt", ""),
            node.raw_attributes.get("src", ""),
        ]
    ).casefold()
    document_words = any(
        word in context
        for word in ("사진", "문서", "첨부", "고지서", "명세서", "photo", "document")
    )
    if node.tag in {"img", "embed", "object", "iframe"}:
        return document_words or bool(_DOCUMENT_EXTENSIONS.search(context))
    return node.tag == "button" and document_words and any(
        word in context for word in ("보기", "열람", "열기", "view", "open")
    )


def _is_structured_parcel_status(node: _Node) -> bool:
    if node.tag not in {"dl", "table", "progress"}:
        return False
    text = _node_text(node).casefold()
    return any(
        phrase in text
        for phrase in ("배송 상태", "배송 현황", "현재 위치", "배송중", "배송 완료", "배달 완료")
    )


def _classify(source: str, nodes: list[_Node]) -> tuple[PageElement, ...]:
    visible = [node for node in nodes if not node.ignored]
    labels = [node for node in visible if node.tag == "label"]
    by_sequence: dict[int, PageElement] = {}

    input_roots: dict[int, _Node] = {
        node.sequence: node for node in visible if node.tag == "form"
    }
    for input_node in visible:
        if input_node.tag not in _INPUT_TAGS or _has_ancestor(input_node, "form"):
            continue
        root = _nearest_input_root(input_node)
        input_roots[root.sequence] = root

    for root in sorted(input_roots.values(), key=lambda item: item.start):
        classified = _classify_input_root(root, labels)
        if classified is None:
            if root.tag == "form" and _is_payment_request(_node_text(root)):
                by_sequence[root.sequence] = _element(
                    source, root, EnvDoubt.PAYMENT
                )
            continue
        doubt, fields = classified
        by_sequence[root.sequence] = _element(source, root, doubt, fields)

    for node in visible:
        if node.tag == "a":
            doubt = _link_doubt(node)
            if doubt is not None:
                by_sequence[node.sequence] = _element(source, node, doubt)
            continue
        if node.tag == "button" and not _has_ancestor(node, "form"):
            if _is_payment_request(_node_text(node)):
                by_sequence[node.sequence] = _element(source, node, EnvDoubt.PAYMENT)
            elif _is_document_control(node):
                by_sequence[node.sequence] = _element(
                    source, node, EnvDoubt.DOCUMENT_VIEW
                )
            continue
        if _is_document_control(node):
            by_sequence[node.sequence] = _element(
                source, node, EnvDoubt.DOCUMENT_VIEW
            )
            continue
        if _is_structured_parcel_status(node):
            by_sequence[node.sequence] = _element(
                source, node, EnvDoubt.PARCEL_WIDGET
            )
            continue
        if node.tag in {"p", "div", "section"} and not node.children:
            direct_text = _node_text(node)
            if _is_payment_request(direct_text):
                by_sequence[node.sequence] = _element(
                    source, node, EnvDoubt.PAYMENT
                )

    return tuple(sorted(by_sequence.values(), key=lambda item: item.start))


def inspect_html(info: str) -> PageInspection:
    """Preserve source-backed HTML elements without network or script execution."""

    if not info:
        return PageInspection(text="", elements=(), failure=FailureCode.EMPTY_INPUT)
    if len(info.encode("utf-8")) > MAX_HTML_BYTES:
        return PageInspection(text="", elements=(), failure=FailureCode.INPUT_TOO_LARGE)

    collector = _Collector(info)
    try:
        collector.feed(info)
        collector.close()
    except _InspectionLimit:
        pass
    except Exception:
        collector.malformed_important = True
    finally:
        collector.finish()

    text = _normalize_text(collector.page_chunks)[:MAX_PAGE_TEXT_CHARS]
    elements = _classify(info, collector.nodes)
    partial = collector.limit_reached or collector.malformed_important
    if partial:
        failure = FailureCode.PARTIAL_CONTENT
    elif not text and not elements:
        failure = FailureCode.EMPTY_INPUT
    else:
        failure = None
    return PageInspection(text=text, elements=elements, failure=failure)


def select_env_doubt(elements: tuple[PageElement, ...]) -> EnvDoubt:
    """Select the first source-ordered candidate after collector deduplication."""

    if not elements:
        return EnvDoubt.NONE
    return min(elements, key=lambda item: item.start).doubt
