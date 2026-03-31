from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParseErrorDetail:
    kind: str
    summary: str
    suggestion: str | None = None
    example: str | None = None

    def render(self) -> str:
        message = f"parse error: {self.summary}"
        if self.suggestion:
            message = f"{message} {self.suggestion}"
        if self.example:
            message = f"{message} {self.example}"
        return message

    def as_artifact(self) -> dict[str, str]:
        artifact = {
            "kind": self.kind,
            "summary": self.summary,
            "message": self.render(),
        }
        if self.suggestion:
            artifact["suggestion"] = self.suggestion
        if self.example:
            artifact["example"] = self.example
        return artifact


def make_parse_error(
    kind: str,
    summary: str,
    *,
    suggestion: str | None = None,
    example: str | None = None,
) -> ParseErrorDetail:
    return ParseErrorDetail(kind=kind, summary=summary, suggestion=suggestion, example=example)


def invalid_heredoc_error_detail(command: str) -> ParseErrorDetail:
    return ParseErrorDetail(kind="invalid_heredoc", summary=f"invalid heredoc syntax for `{command or 'command'}`")


def unsupported_or_error_detail(fallback_text: str) -> ParseErrorDetail:
    fallback_suffix = f"`|| {fallback_text}`" if fallback_text else "`||`"
    return ParseErrorDetail(
        kind="unsupported_or_fallback",
        summary=f"unsupported {fallback_suffix} fallback.",
        suggestion="Supported forms are `|| true`, `|| :`, and `|| help`. Otherwise remove the `|| ...` tail and run the main command directly, or rewrite it as `;` / `&&` explicitly.",
    )


def format_unsupported_or_error(fallback_text: str) -> str:
    return unsupported_or_error_detail(fallback_text).render()


def shlex_parse_error_detail(cmd: str, message: str) -> ParseErrorDetail:
    if message == "No escaped character":
        if "-exec" in cmd or "\\;" in cmd or "\\(" in cmd or "\\)" in cmd:
            return ParseErrorDetail(
                kind="unsupported_find_escape",
                summary="shell-escaped find syntax is not supported in that form.",
                suggestion="Use a limited form like `find /workspace -type f -exec grep -l TODO {} \\;`, `find ... | xargs grep -l TODO`, or `grep -l TODO /workspace`.",
            )
        return ParseErrorDetail(
            kind="stray_escape",
            summary="stray backslash escape; use plain commands only",
        )
    return ParseErrorDetail(kind="shlex_error", summary=message)


def format_shlex_parse_error(cmd: str, message: str) -> str:
    return shlex_parse_error_detail(cmd, message).render()
