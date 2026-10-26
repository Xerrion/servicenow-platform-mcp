"""Bounded lexical discovery of native ``fd_data.producer.named_path`` references.

This is not a JavaScript syntax validator or scope resolver. It accepts direct
ASCII identifier members, whitespace/comments between members, ordinary quoted
strings, non-nested regexp character classes, and template interpolations.
Bare roots, computed/optional members and detectable root bindings are rejected.
Parenthesized references cannot continue with further member access.
Escaped identifiers, legacy HTML comments, JSX/E4X, private-name syntax, regexp
``v`` sets, and ambiguous slashes (after closing braces or contextual keywords)
are unsupported rather than guessed. Unbalanced delimiters and unterminated
literals also fail closed. Dynamic ``with`` scopes containing references reject.
No script is evaluated. Limits are 65,000 characters, 100 references and 32
nested template interpolations; producer identity/scope belongs to the caller.
"""

import json
import re
from dataclasses import dataclass

_MAX_CODE_LENGTH = 65_000
_MAX_REFERENCES = 100
_MAX_TEMPLATE_DEPTH = 32
_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\Z")
_NUMBER = re.compile(
    r"(?:0[xX][0-9a-fA-F_]+n?|0[bB][01_]+n?|0[oO][0-7_]+n?|"
    r"(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)"
    r"(?:[eE][+-]?[0-9_]+)?n?)"
)
_LINE_END = "\r\n\u2028\u2029"
_SPACE = " \t\v\f\r\n\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_CONTROL_HEADS = {"if", "while", "for", "with", "switch", "catch"}
_EXPRESSION_PREFIXES = {
    "return",
    "throw",
    "case",
    "delete",
    "void",
    "typeof",
    "new",
    "in",
    "instanceof",
    "else",
    "do",
    "break",
    "continue",
    "debugger",
}
_OPERATORS = (
    ">>>=",
    "===",
    "!==",
    "**=",
    "&&=",
    "||=",
    "??=",
    "<<=",
    ">>=",
    ">>>",
    "=>",
    "?.",
    "++",
    "--",
    "==",
    "!=",
    "<=",
    ">=",
    "&&",
    "||",
    "??",
    "+=",
    "-=",
    "*=",
    "%=",
    "&=",
    "|=",
    "^=",
    "**",
    "<<",
    ">>",
    "...",
)


@dataclass(frozen=True, slots=True)
class ScriptReference:
    """Offsets delimit the complete direct expression; ``path`` excludes producer."""

    start: int
    end: int
    producer: str
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    start: int
    end: int
    kind: str = "punctuation"


def _identifier_start(char: str) -> bool:
    return char in "_$" or char.isidentifier()


def _identifier_part(char: str) -> bool:
    return char in "$\u200c\u200d" or ("a" + char).isidentifier()


class _Lexer:
    def __init__(self, code: str) -> None:
        self.code: str = code
        self.tokens: list[_Token] = []

    def _emit(self, start: int, end: int, kind: str = "punctuation") -> None:
        self.tokens.append(_Token(self.code[start:end], start, end, kind))

    def _quoted(self, start: int) -> int:
        quote = self.code[start]
        index = start + 1
        while index < len(self.code):
            char = self.code[index]
            if char == quote:
                return index + 1
            if char in _LINE_END:
                raise ValueError("Unterminated JavaScript string literal")
            if char == "\\":
                index += 1
                if self.code[index : index + 2] == "\r\n":
                    index += 1
            index += 1
        raise ValueError("Unterminated JavaScript string literal")

    def _regexp(self, start: int) -> int:
        index = start + 1
        in_class = False
        while index < len(self.code):
            char = self.code[index]
            if char in _LINE_END:
                raise ValueError("Unterminated JavaScript regexp literal")
            if char == "\\":
                if self.code.startswith("\\q{", index):
                    raise ValueError("Regexp Unicode string sets are unsupported")
                if index + 1 == len(self.code) or self.code[index + 1] in _LINE_END:
                    raise ValueError("Unterminated JavaScript regexp escape")
                index += 2
                continue
            if char == "[":
                if in_class:
                    raise ValueError("Nested regexp character classes are unsupported")
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                index += 1
                flags_start = index
                while index < len(self.code) and _identifier_part(self.code[index]):
                    index += 1
                flags = self.code[flags_start:index]
                if "v" in flags or any(not char.isascii() for char in flags):
                    raise ValueError("Unsupported JavaScript regexp flags")
                return index
            index += 1
        raise ValueError("Unterminated JavaScript regexp literal")

    def _template(self, start: int, depth: int) -> int:
        if depth > _MAX_TEMPLATE_DEPTH:
            raise ValueError("Script exceeds 32 nested template interpolations")
        self._emit(start, start + 1, "literal")
        index = start + 1
        while index < len(self.code):
            if self.code[index] == "\\":
                index += 2
            elif self.code[index] == "`":
                self._emit(index, index + 1, "literal")
                return index + 1
            elif self.code.startswith("${", index):
                self._emit(index, index + 2)
                index = self.scan(index + 2, interpolation=True, depth=depth)
            else:
                index += 1
        raise ValueError("Unterminated JavaScript template literal")

    def scan(
        self, index: int = 0, *, interpolation: bool = False, depth: int = 0
    ) -> int:
        # None means that the lexical slash goal needs a full statement parser.
        can_regexp: bool | None = True
        delimiters: list[tuple[str, bool]] = []
        while index < len(self.code):
            char = self.code[index]
            if char in _SPACE:
                index += 1
                continue
            if self.code.startswith("//", index):
                index += 2
                while index < len(self.code) and self.code[index] not in _LINE_END:
                    index += 1
                continue
            if self.code.startswith("/*", index):
                end = self.code.find("*/", index + 2)
                if end < 0:
                    raise ValueError("Unterminated JavaScript block comment")
                index = end + 2
                continue
            if self.code.startswith(("<!--", "-->"), index):
                raise ValueError("Legacy HTML script comments are unsupported")
            previous = self.tokens[-1].text if self.tokens else ""
            if char in "'\"":
                end = self._quoted(index)
                self._emit(index, end, "literal")
                can_regexp = False
            elif char == "`":
                end = self._template(index, depth + 1)
                can_regexp = False
            elif _identifier_start(char):
                end = index + 1
                while end < len(self.code) and _identifier_part(self.code[end]):
                    end += 1
                word = self.code[index:end]
                self._emit(index, end, "identifier")
                if previous in {".", "?."}:
                    can_regexp = False
                elif word in {"await", "yield", "of"}:
                    can_regexp = None
                elif previous in {"break", "continue"} and not any(
                    end in self.code[self.tokens[-2].end : index] for end in _LINE_END
                ):
                    can_regexp = True
                else:
                    can_regexp = word in _EXPRESSION_PREFIXES
            elif (char.isascii() and char.isdigit()) or (
                char == "."
                and self.code[index + 1 : index + 2] in "0123456789"
                and index + 1 < len(self.code)
            ):
                match = _NUMBER.match(self.code, index)
                if match is None:
                    raise ValueError("Unsupported JavaScript numeric syntax")
                end = match.end()
                if end < len(self.code) and _identifier_start(self.code[end]):
                    raise ValueError("Unsupported JavaScript numeric syntax")
                self._emit(index, end, "literal")
                can_regexp = False
            elif char == "/":
                if can_regexp is None:
                    raise ValueError(
                        "Ambiguous JavaScript slash context is unsupported"
                    )
                if can_regexp:
                    end = self._regexp(index)
                    self._emit(index, end, "literal")
                    can_regexp = False
                else:
                    end = index + (2 if self.code.startswith("/=", index) else 1)
                    self._emit(index, end)
                    can_regexp = True
            elif char in "([{":
                control = char == "(" and (
                    previous in _CONTROL_HEADS
                    and (
                        len(self.tokens) < 2 or self.tokens[-2].text not in {".", "?."}
                    )
                    or previous == "await"
                    and len(self.tokens) >= 2
                    and self.tokens[-2].text == "for"
                    and (
                        len(self.tokens) < 3 or self.tokens[-3].text not in {".", "?."}
                    )
                )
                delimiters.append((char, control))
                end = index + 1
                self._emit(index, end)
                can_regexp = True
            elif char in ")]}":
                end = index + 1
                if char == "}" and not delimiters and interpolation:
                    self._emit(index, end)
                    return end
                expected = {")": "(", "]": "[", "}": "{"}[char]
                if not delimiters or delimiters[-1][0] != expected:
                    raise ValueError("Unbalanced JavaScript delimiters")
                _, control = delimiters.pop()
                self._emit(index, end)
                can_regexp = None if char == "}" else control
            elif char in ".,;:?=+-*%&|^!~<>":
                if char == "<" and can_regexp is not False:
                    raise ValueError("JSX/E4X-like expression syntax is unsupported")
                operator = next(
                    (op for op in _OPERATORS if self.code.startswith(op, index)), char
                )
                end = index + len(operator)
                self._emit(index, end)
                if operator not in {"++", "--"}:
                    can_regexp = operator not in {".", "?."}
            else:
                raise ValueError("Unsupported JavaScript lexical syntax")
            index = end
        if interpolation or delimiters:
            raise ValueError("Unbalanced JavaScript delimiters")
        return index


def _discover(code: str) -> list[tuple[ScriptReference, int, int]]:
    if len(code) > _MAX_CODE_LENGTH:
        raise ValueError("Script exceeds 65000 characters")
    lexer = _Lexer(code)
    _ = lexer.scan()
    tokens = lexer.tokens
    found: list[tuple[ScriptReference, int, int]] = []
    index = 0
    while index < len(tokens):
        root = tokens[index]
        previous = tokens[index - 1].text if index else ""
        if (
            root.kind != "identifier"
            or root.text != "fd_data"
            or previous in {".", "?."}
        ):
            index += 1
            continue
        # A colon after a conditional operand is a live bare-root use, not a key.
        if (
            previous in {"", "{", ",", ";", "}"}
            and index + 1 < len(tokens)
            and tokens[index + 1].text == ":"
        ):
            index += 1
            continue
        members: list[_Token] = []
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor].text == ".":
            cursor += 1
            if (
                cursor == len(tokens)
                or tokens[cursor].kind != "identifier"
                or not _IDENTIFIER.fullmatch(tokens[cursor].text)
            ):
                raise ValueError("fd_data requires direct named ASCII member paths")
            members.append(tokens[cursor])
            cursor += 1
        if not members:
            raise ValueError("Bare, aliased or shadowed fd_data is unsupported")
        if cursor < len(tokens) and tokens[cursor].text in {"[", "?.", "..."}:
            raise ValueError(
                "Computed or optional fd_data member access is unsupported"
            )
        # Parentheses must not conceal a computed/optional continuation. Stop
        # before a call/control argument's closing paren: its result is distinct.
        left = index
        suffix = cursor
        while (
            left
            and tokens[left - 1].text == "("
            and suffix < len(tokens)
            and tokens[suffix].text == ")"
        ):
            before = tokens[left - 2] if left > 1 else None
            if before is not None and (
                before.kind == "literal"
                or before.text in {")", "]"}
                or before.kind == "identifier"
                and before.text not in _EXPRESSION_PREFIXES | {"await", "yield"}
            ):
                break
            left -= 1
            suffix += 1
        if (
            suffix != cursor
            and suffix < len(tokens)
            and tokens[suffix].text
            in {
                ".",
                "[",
                "?.",
            }
        ):
            raise ValueError(
                "Parenthesized fd_data member continuations are unsupported"
            )
        producer = members[0]
        found.append(
            (
                ScriptReference(
                    root.start,
                    members[-1].end,
                    producer.text,
                    tuple(member.text for member in members[1:]),
                ),
                producer.start,
                producer.end,
            )
        )
        if len(found) > _MAX_REFERENCES:
            raise ValueError("Script exceeds 100 fd_data references")
        index = cursor
    if found and any(
        token.kind == "identifier"
        and token.text == "with"
        and (index == 0 or tokens[index - 1].text not in {".", "?."})
        and index + 1 < len(tokens)
        and tokens[index + 1].text == "("
        for index, token in enumerate(tokens)
    ):
        raise ValueError("Dynamic with scopes containing fd_data are unsupported")
    return found


def references(code: str) -> list[ScriptReference]:
    """Find direct references in source order, raising ``ValueError`` if unsafe.

    Producer-only references have an empty path. Callers must validate producer
    identity and named paths; declarations using bare ``fd_data`` are rejected,
    not treated as evidence that later references can safely be ignored.
    """
    return [reference for reference, _, _ in _discover(code)]


def _producer_edits(
    code: str, replacements: dict[str, str]
) -> list[tuple[int, int, str]]:
    spans = _discover(code)
    for value in replacements.values():
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("Replacement producer must be a named ASCII identifier")
    edits: list[tuple[int, int, str]] = []
    length = len(code)
    for reference, start, end in spans:
        replacement = replacements.get(reference.producer, reference.producer)
        if replacement != reference.producer:
            length += len(replacement) - (end - start)
            edits.append((start, end, replacement))
    if length > _MAX_CODE_LENGTH:
        raise ValueError("Rewritten script exceeds 65000 characters")
    return edits


def _splice(source: str, edits: list[tuple[int, int, str]]) -> str:
    if not edits:
        return source
    parts: list[str] = []
    offset = 0
    for start, end, replacement in edits:
        parts.extend((source[offset:start], replacement))
        offset = end
    parts.append(source[offset:])
    return "".join(parts)


def rewrite(code: str, replacements: dict[str, str]) -> str:
    """Simultaneously replace mapped producer tokens, preserving all other text.

    Unmapped producers remain unchanged. Replacement values must be ASCII
    identifiers; lexical errors and output above 65,000 characters raise
    ``ValueError`` before a rewritten script can be returned.
    """
    return _splice(code, _producer_edits(code, replacements))


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Script mirror contains duplicate JSON object keys")
        value[key] = item
    return value


def _json_constant(_value: str) -> None:
    raise ValueError("Script mirror contains a non-JSON numeric constant")


def _json_string_end(source: str, start: int) -> int:
    # Only called after json.loads has validated the complete document.
    index = start + 1
    while source[index] != '"':
        index += 2 if source[index] == "\\" else 1
    return index + 1


def _json_space(source: str, start: int) -> int:
    while start < len(source) and source[start] in " \t\r\n":
        start += 1
    return start


def _json_script_spans(source: str) -> list[tuple[str, int, int]]:
    """Locate only entry.script strings in an already validated native map."""
    found: list[tuple[str, int, int]] = []
    depth = 0
    field = ""
    index = 0
    while index < len(source):
        char = source[index]
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == '"':
            end = _json_string_end(source, index)
            after = _json_space(source, end)
            if depth in {1, 2} and source[after : after + 1] == ":":
                key = json.loads(source[index:end])
                if depth == 1:
                    field = key
                elif key == "script":
                    start = _json_space(source, after + 1)
                    found.append((field, start, _json_string_end(source, start)))
            index = end
            continue
        index += 1
    return found


def _json_string_boundaries(source: str, start: int, end: int) -> list[int]:
    """Map decoded code-point boundaries to original encoded JSON offsets."""
    index = start + 1
    boundaries = [index]
    while index < end - 1:
        if source[index] != "\\":
            index += 1
        elif source[index + 1] != "u":
            index += 2
        else:
            value = int(source[index + 2 : index + 6], 16)
            index += 6
            if 0xD800 <= value <= 0xDBFF and source[index : index + 2] == "\\u":
                low = int(source[index + 2 : index + 6], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    index += 6
        boundaries.append(index)
    return boundaries


def rewrite_mirror(
    mirror: str,
    replacements: dict[str, str],
    *,
    entry_replacements: dict[str, dict[str, str]] | None = None,
) -> str:
    """Rewrite entry.script producers in a native scriptAsJsonString object.

    JSON must be an object of objects with string-valued ``script`` properties;
    duplicate keys and non-JSON constants reject with ``ValueError``. All other
    properties, source whitespace/order and unrelated string escapes are left
    byte-for-byte intact. Escaped producer characters alone may be replaced by
    their new ASCII identifier. Matching this mirror to the actual native map
    and validating other entry metadata remain the caller's responsibility.

    When ``entry_replacements`` is supplied, each entry uses only its own map;
    unspecified entries remain unchanged and ``replacements`` is not a fallback.
    """
    try:
        value: object = json.loads(
            mirror, object_pairs_hook=_json_object, parse_constant=_json_constant
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Script mirror must be valid JSON") from exc
    if not isinstance(value, dict):
        raise TypeError("Script mirror must be a native script-map object")
    scripts: dict[str, str] = {}
    for field, entry in value.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("script"), str):
            raise TypeError("Script mirror entries must contain a script string")
        scripts[field] = entry["script"]
    edits: list[tuple[int, int, str]] = []
    for field, start, end in _json_script_spans(mirror):
        code = scripts[field]
        mapping = (
            replacements
            if entry_replacements is None
            else entry_replacements.get(field, {})
        )
        producers = _producer_edits(code, mapping)
        if not producers:
            continue
        boundaries = _json_string_boundaries(mirror, start, end)
        if len(boundaries) != len(code) + 1:
            raise ValueError("Script mirror has an unsupported string encoding")
        edits.extend(
            (boundaries[left], boundaries[right], replacement)
            for left, right, replacement in producers
        )
    return _splice(mirror, edits)
