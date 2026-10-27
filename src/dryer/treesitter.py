"""Shared tree-sitter helpers."""

import os
from functools import lru_cache

OFFLINE_ENV = "QUALITY_TOOLS_OFFLINE"


class GrammarUnavailable(RuntimeError):
    """A grammar is not on disk and the run is not allowed to download it."""


def offline() -> bool:
    return os.environ.get(OFFLINE_ENV, "") not in ("", "0")


@lru_cache(maxsize=None)
def parser_for(language: str):
    import tree_sitter_language_pack as pack

    if offline():
        # get_parser downloads on a cache miss too, so check before calling it.
        if language not in pack.downloaded_languages():
            raise GrammarUnavailable(
                f"tree-sitter grammar '{language}' is not in {pack.cache_dir()} "
                f"and {OFFLINE_ENV} forbids downloading it. Download it once with "
                "network access, or set TREE_SITTER_LANGUAGE_PACK_CACHE_DIR to a "
                "directory that already holds it."
            )
        return pack.get_parser(language)
    pack.download([language])
    return pack.get_parser(language)


def parse(source: str, language: str):
    data = source.encode("utf-8")
    tree = parser_for(language).parse(data)
    return data, tree


def node_text(data: bytes, node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8")


def start_line(node) -> int:
    return node.start_point[0] + 1


def end_line(node) -> int:
    row, col = node.end_point
    if col == 0:
        return max(start_line(node), row)
    return row + 1


def descendants(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def child_of_type(node, *types: str):
    for child in node.children:
        if child.type in types:
            return child
    return None
