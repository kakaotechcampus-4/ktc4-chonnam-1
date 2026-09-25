"""Execute-free inspection of HTML supplied by the isolated page collector."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
import json
import re
from time import monotonic

from ai.types import EnvDoubt, FailureCode


MAX_HTML_BYTES = 131_072
MAX_ELEMENTS = 2_000
MAX_PAGE_TEXT_CHARS = 16_000
MAX_DEPTH = 64
INSPECTION_TIMEOUT_SECONDS = 0.100
MAX_INSPECTION_BYTES = 262_144
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
_NUMERIC_CHARACTER_REFERENCE = re.compile(r"&#(?:x([0-9a-f]+)|([0-9]+));?", re.I)


@dataclass(frozen=True)
class PageField:
    attributes: dict[str, str]
    labels: tuple[str, ...]


@dataclass(frozen=True)
class PageElement:
    element_id: str
    doubt: EnvDoubt
    evidence: str
    start: int
    tag: str
    attributes: dict[str, str]
    text: str
    fields: tuple[PageField, ...] = ()


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


class _InspectionTimeout(Exception):
    pass


def _check_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise _InspectionTimeout


class _Collector(HTMLParser):
    def __init__(self, source: str, deadline: float) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.deadline = deadline
        self.nodes: list[_Node] = []
        self.stack: list[_Node] = []
        self.page_chunks: list[str] = []
        self.text_chars = 0
        self.limit_reached = False
        self.malformed_important = False
        self.processed_end = 0
        self._line_offsets = [0, *(index + 1 for index, char in enumerate(source) if char == "\n")]

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def _tag_end(self, start: int) -> int:
        closing = self.source.find(">", start)
        return len(self.source) if closing < 0 else closing + 1

    def _new_node(
        self, tag: str, attrs: list[tuple[str, str | None]], *, closes_itself: bool
    ) -> None:
        self.processed_end = max(self.processed_end, self._offset())
        _check_deadline(self.deadline)
        if len(self.nodes) >= MAX_ELEMENTS:
            self.limit_reached = True
            raise _InspectionLimit
        if len(self.stack) + 1 > MAX_DEPTH:
            self.limit_reached = True
            raise _InspectionLimit

        start = self._offset()
        start_tag_text = self.get_starttag_text()
        start_tag_end = (
            start + len(start_tag_text)
            if start_tag_text is not None
            else self._tag_end(start)
        )
        if start_tag_text is not None:
            for match in _NUMERIC_CHARACTER_REFERENCE.finditer(start_tag_text):
                codepoint = int(match.group(1) or match.group(2), 16 if match.group(1) else 10)
                if 0xD800 <= codepoint <= 0xDFFF:
                    self.malformed_important = True
                    break
        raw_attributes: dict[str, str] = {}
        for name, value in attrs:
            _check_deadline(self.deadline)
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
        self.processed_end = max(self.processed_end, node.start_tag_end)

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._new_node(tag.lower(), attrs, closes_itself=False)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._new_node(tag.lower(), attrs, closes_itself=True)

    def handle_endtag(self, tag: str) -> None:
        start = self._offset()
        self.processed_end = max(self.processed_end, start)
        _check_deadline(self.deadline)
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
            self.processed_end = max(self.processed_end, self._tag_end(start))
            return

        end = self._tag_end(start)
        intervening = self.stack[match + 1 :]
        matched = self.stack[match]
        for node in intervening:
            _check_deadline(self.deadline)
            node.incomplete = True
            node.end = end
            if node.tag == "form" and not node.ignored:
                self.malformed_important = True
        if matched.tag == "form" and intervening and not matched.ignored:
            matched.incomplete = True
            self.malformed_important = True
        matched.end = end
        del self.stack[match:]
        self.processed_end = max(self.processed_end, end)

    def handle_data(self, data: str) -> None:
        self.processed_end = max(self.processed_end, self._offset())
        _check_deadline(self.deadline)
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
            node.end = max(node.start_tag_end, self.processed_end)
            node.incomplete = True
            if node.tag == "form" and not node.ignored:
                self.malformed_important = True


def _normalize_text(parts: list[str] | tuple[str, ...] | str) -> str:
    if isinstance(parts, str):
        value = parts
    else:
        value = " ".join(parts)
    return " ".join(value.split())


def _json_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _inspection_base_size(text: str, failure: FailureCode | None) -> int:
    return _json_size(asdict(PageInspection(text=text, elements=(), failure=failure)))


def _fit_text(text: str, failure: FailureCode) -> tuple[str, bool]:
    if _inspection_base_size(text, failure) <= MAX_INSPECTION_BYTES:
        return text, False
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _inspection_base_size(text[:middle], failure) <= MAX_INSPECTION_BYTES:
            low = middle
        else:
            high = middle - 1
    return text[:low], True


def _node_text(node: _Node, deadline: float) -> str:
    parts: list[str] = []
    pending = list(reversed(node.content))
    while pending:
        _check_deadline(deadline)
        item = pending.pop()
        if isinstance(item, str):
            parts.append(item)
        elif not item.ignored:
            pending.extend(reversed(item.content))
    return _normalize_text(parts)


def _descendants(node: _Node, deadline: float) -> list[_Node]:
    found: list[_Node] = []
    pending = list(reversed(node.children))
    while pending:
        _check_deadline(deadline)
        child = pending.pop()
        if child.ignored:
            continue
        found.append(child)
        pending.extend(reversed(child.children))
    return found


def _has_ancestor(node: _Node, tag: str, deadline: float) -> bool:
    current = node.parent
    while current is not None:
        _check_deadline(deadline)
        if current.tag == tag:
            return True
        current = current.parent
    return False


def _field_labels(
    field_node: _Node, root: _Node, labels: list[_Node], deadline: float
) -> tuple[str, ...]:
    pieces: list[str] = []
    current = field_node.parent
    while current is not None and current is not root.parent:
        _check_deadline(deadline)
        if current.tag == "label":
            pieces.append(_node_text(current, deadline))
            break
        current = current.parent
    field_id = field_node.raw_attributes.get("id")
    if field_id:
        for label in labels:
            _check_deadline(deadline)
            if label.raw_attributes.get("for") == field_id:
                pieces.append(_node_text(label, deadline))
    return tuple(dict.fromkeys(pieces))


def _field_context(
    field_node: _Node, root: _Node, labels: list[_Node], deadline: float
) -> str:
    return _normalize_text([
        *(field_node.attributes.get(name, "")
          for name in ("type", "name", "autocomplete", "aria-label")),
        *_field_labels(field_node, root, labels, deadline),
    ]).casefold()


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


def _classify_input_root(
    root: _Node, labels: list[_Node], deadline: float
) -> tuple[EnvDoubt, list[_Node]] | None:
    fields = [
        node
        for node in [root, *_descendants(root, deadline)]
        if node.tag in _INPUT_TAGS and not node.ignored
    ]
    if not fields:
        return None

    root_text = _node_text(root, deadline).casefold()
    candidates: list[tuple[int, int, EnvDoubt, _Node]] = []
    priority = {
        EnvDoubt.LOGIN_FORM: 0,
        EnvDoubt.ADDRESS_FORM: 1,
        EnvDoubt.PARCEL_WIDGET: 2,
        EnvDoubt.PERSONAL_FORM: 3,
    }
    for input_node in fields:
        _check_deadline(deadline)
        context = _field_context(input_node, root, labels, deadline)
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


def _nearest_input_root(node: _Node, deadline: float) -> _Node:
    current = node.parent
    while current is not None:
        _check_deadline(deadline)
        if current.tag in _INPUT_CONTAINERS:
            return current
        current = current.parent
    return node


def _element(
    source: str, root: _Node, doubt: EnvDoubt,
    fields: list[_Node] = (), labels: list[_Node] = (),
    *, deadline: float,
) -> PageElement:
    _check_deadline(deadline)
    end = root.end if root.end is not None else len(source)
    page_fields: list[PageField] = []
    for input_node in fields:
        _check_deadline(deadline)
        page_fields.append(PageField(
            attributes={name: value for name, value in input_node.attributes.items()
                        if name in {"type", "name", "autocomplete", "aria-label"}},
            labels=_field_labels(input_node, root, labels, deadline),
        ))
    return PageElement(
        element_id=f"element-{root.sequence:04d}",
        doubt=doubt,
        evidence=source[root.start:end],
        start=root.start,
        tag=root.tag,
        attributes=dict(root.attributes),
        text=_node_text(root, deadline),
        fields=tuple(page_fields),
    )


def _link_doubt(node: _Node, deadline: float) -> EnvDoubt | None:
    href = node.raw_attributes.get("href", "")
    if not href:
        return None
    context = _normalize_text(
        [_node_text(node, deadline), node.raw_attributes.get("aria-label", "")]
    ).casefold()
    app_words = any(word in context for word in ("앱", "어플", "app", "설치"))
    download_words = any(word in context for word in ("다운로드", "download", "설치"))
    if app_words and download_words:
        return EnvDoubt.APP_LINK
    document_words = any(
        word in context
        for word in ("사진", "문서", "파일", "첨부", "고지서", "명세서", "photo", "document")
    )
    if _DOCUMENT_EXTENSIONS.search(href) or document_words:
        return EnvDoubt.DOCUMENT_VIEW
    return None


def _is_document_control(node: _Node, deadline: float) -> bool:
    context = _normalize_text(
        [
            _node_text(node, deadline),
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


def _is_structured_parcel_status(node: _Node, deadline: float) -> bool:
    if node.tag not in {"dl", "table", "progress"}:
        return False
    text = _node_text(node, deadline).casefold()
    return any(
        phrase in text
        for phrase in ("배송 상태", "배송 현황", "현재 위치", "배송중", "배송 완료", "배달 완료")
    )


def _classify(source: str, nodes: list[_Node], deadline: float):
    visible: list[_Node] = []
    labels: list[_Node] = []
    for node in nodes:
        _check_deadline(deadline)
        if not node.ignored:
            visible.append(node)
            if node.tag == "label":
                labels.append(node)

    input_roots: dict[int, _Node] = {
        node.sequence: node for node in visible if node.tag == "form"
    }
    for input_node in visible:
        _check_deadline(deadline)
        if input_node.tag not in _INPUT_TAGS or _has_ancestor(input_node, "form", deadline):
            continue
        root = _nearest_input_root(input_node, deadline)
        input_roots[root.sequence] = root

    emitted: set[int] = set()
    for node in visible:
        _check_deadline(deadline)
        if node.sequence in input_roots:
            classified = _classify_input_root(node, labels, deadline)
            if classified is not None:
                doubt, fields = classified
                yield _element(
                    source, node, doubt, fields, labels, deadline=deadline
                )
                emitted.add(node.sequence)
            elif node.tag == "form" and _is_payment_request(_node_text(node, deadline)):
                yield _element(source, node, EnvDoubt.PAYMENT, deadline=deadline)
                emitted.add(node.sequence)
        if node.sequence in emitted:
            continue
        if node.tag == "a":
            doubt = _link_doubt(node, deadline)
            if doubt is not None:
                yield _element(source, node, doubt, deadline=deadline)
            continue
        if node.tag == "button" and not _has_ancestor(node, "form", deadline):
            if _is_payment_request(_node_text(node, deadline)):
                yield _element(source, node, EnvDoubt.PAYMENT, deadline=deadline)
            elif _is_document_control(node, deadline):
                yield _element(source, node, EnvDoubt.DOCUMENT_VIEW, deadline=deadline)
            continue
        if _is_document_control(node, deadline):
            yield _element(source, node, EnvDoubt.DOCUMENT_VIEW, deadline=deadline)
            continue
        if _is_structured_parcel_status(node, deadline):
            yield _element(source, node, EnvDoubt.PARCEL_WIDGET, deadline=deadline)
            continue
        if node.tag in {"p", "div", "section"} and not node.children:
            direct_text = _node_text(node, deadline)
            if _is_payment_request(direct_text):
                yield _element(source, node, EnvDoubt.PAYMENT, deadline=deadline)


def inspect_html(info: str) -> PageInspection:
    """Preserve source-backed HTML elements without network or script execution."""

    deadline = monotonic() + INSPECTION_TIMEOUT_SECONDS
    _check_deadline(deadline)
    if not info:
        return PageInspection(text="", elements=(), failure=FailureCode.EMPTY_INPUT)
    if len(info) > MAX_HTML_BYTES:
        return PageInspection(text="", elements=(), failure=FailureCode.INPUT_TOO_LARGE)
    try:
        size = len(info.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError("HTML input must be valid UTF-8 text") from error
    if size > MAX_HTML_BYTES:
        return PageInspection(text="", elements=(), failure=FailureCode.INPUT_TOO_LARGE)

    collector = _Collector(info, deadline)
    timed_out = False
    try:
        collector.feed(info)
        collector.close()
        collector.processed_end = len(info)
    except _InspectionTimeout:
        timed_out = True
    except _InspectionLimit:
        pass
    except Exception:
        collector.malformed_important = True
    finally:
        collector.finish()

    text = _normalize_text(collector.page_chunks)[:MAX_PAGE_TEXT_CHARS]
    budget_failure = FailureCode.TIMEOUT if timed_out else FailureCode.PARTIAL_CONTENT
    text, output_limited = _fit_text(text, budget_failure)
    base_size = _inspection_base_size(text, budget_failure)
    elements: list[PageElement] = []
    element_sizes: list[int] = []
    elements_size = 0
    if not timed_out:
        try:
            _check_deadline(deadline)
            for element in _classify(info, collector.nodes, deadline):
                _check_deadline(deadline)
                try:
                    element_size = _json_size(asdict(element))
                except UnicodeEncodeError:
                    output_limited = True
                    continue
                _check_deadline(deadline)
                comma_size = 1 if elements else 0
                serialized_size = comma_size + element_size
                if base_size + elements_size + serialized_size <= MAX_INSPECTION_BYTES:
                    elements.append(element)
                    element_sizes.append(serialized_size)
                    elements_size += serialized_size
                else:
                    output_limited = True
            _check_deadline(deadline)
        except _InspectionTimeout:
            timed_out = True
    partial = collector.limit_reached or collector.malformed_important or output_limited
    if timed_out:
        failure = FailureCode.TIMEOUT
    elif partial:
        failure = FailureCode.PARTIAL_CONTENT
    elif not text and not elements:
        failure = FailureCode.EMPTY_INPUT
    else:
        failure = None

    total_size = _inspection_base_size(text, failure) + elements_size
    while elements and total_size > MAX_INSPECTION_BYTES:
        elements.pop()
        elements_size -= element_sizes.pop()
        failure = FailureCode.TIMEOUT if timed_out else FailureCode.PARTIAL_CONTENT
        total_size = _inspection_base_size(text, failure) + elements_size
    result = PageInspection(text=text, elements=tuple(elements), failure=failure)
    if _json_size(asdict(result)) > MAX_INSPECTION_BYTES:
        text, _ = _fit_text(text, failure or FailureCode.PARTIAL_CONTENT)
        result = PageInspection(
            text=text,
            elements=(),
            failure=FailureCode.TIMEOUT if timed_out else FailureCode.PARTIAL_CONTENT,
        )
    return result


def select_env_doubt(elements: tuple[PageElement, ...]) -> EnvDoubt:
    """Select the first source-ordered candidate after collector deduplication."""

    if not elements:
        return EnvDoubt.NONE
    return min(elements, key=lambda item: item.start).doubt
