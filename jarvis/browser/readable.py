"""The main content of a web page, without the site around it.

save_page_knowledge used to feed the model ``soup.get_text()`` of the whole
<body> minus <nav>/<footer>/<header>/<aside>. Many sites do not use those
tags: on a Steam guide the model got the store menu, the 30-language
switcher, "Наградить / В избранное / Поделиться", view counters and the
author block, and some of that came back as "achievements".

This is a small, dependency-free cousin of Mozilla's Readability, built on
BeautifulSoup (already a dependency):

1. Drop elements that are never content: scripts, forms, buttons, inputs,
   svg, iframes, and the semantic chrome tags.
2. Drop elements whose ARIA role, id or class names say they are chrome
   (menu, header, footer, sidebar, share, rating, award, language,
   comment, cookie, ...). Class names are split into words, including
   camelCase ("workshopItemControls" -> workshop / item / controls), and
   matched against a word list, so "headerless-article" is not caught by a
   substring test. An element holding more than half of the page's text is
   never dropped, whatever its class says -- a page wrapper called
   "page with_header" must not take the content with it.
3. Drop link farms: lists and blocks whose text is mostly link text
   (menus and language lists that carry no telling class).
4. If the page has an <article> / <main> / role=main with real text, keep
   only that.

Alternatives considered: a site-specific selector table (exact, but every
new site needs code); readability-lxml / trafilatura (better, but new
compiled dependencies for one feature); asking the LLM to ignore the junk
(already in the prompt -- an 8B model obeys only partly, and every junk
line also costs extraction time).
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_NEVER_CONTENT_TAGS = (
    "script", "style", "noscript", "template", "svg", "canvas", "iframe",
    # Not "label": recipe sites often render ingredients as checkbox labels.
    "form", "button", "select", "option", "input", "textarea",
    "dialog", "nav", "footer", "header", "aside", "menu",
)

_CHROME_ROLES = frozenset({
    "navigation", "banner", "contentinfo", "complementary", "menu", "menubar",
    "toolbar", "search", "dialog", "alertdialog", "button", "tablist",
})

# Words in id / class names that mark page chrome. Whole words only.
_CHROME_WORDS = frozenset({
    "nav", "navbar", "navigation", "menu", "menubar", "submenu", "header",
    "footer", "sidebar", "breadcrumb", "breadcrumbs", "toolbar", "topbar",
    "cookie", "cookies", "consent", "gdpr", "banner", "advert", "advertisement",
    "ads", "promo", "sponsored", "share", "sharing", "social", "comment",
    "comments", "rating", "ratings", "vote", "votes", "voting", "award",
    "awards", "reactions", "login", "signin", "signup", "subscribe",
    "newsletter", "popup", "modal", "dropdown", "language", "languages",
    "related", "recommended", "pagination", "pager", "controls", "byline",
    "author", "creator", "creators", "avatar", "favorite", "favorites",
    "favourite", "stats", "statistics", "tags", "btn", "button", "buttons",
})

_MAIN_SELECTORS = ("article", "main", "[role=main]")
_MIN_MAIN_TEXT = 400
_LINK_FARM_DENSITY = 0.6
# A single link is not a farm: a guide entry "detailed guide here: <url>"
# is mostly link text but is content. Menus and language lists have many.
_LINK_FARM_MIN_LINKS = 3
_WORD_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _words(value: str) -> set[str]:
    words: set[str] = set()
    for part in re.split(r"[\s_\-:./]+", value):
        words.update(w.lower() for w in _WORD_SPLIT.findall(part))
    return words


def _attr_words(tag: Any) -> set[str]:
    words: set[str] = set()
    ident = tag.get("id")
    if isinstance(ident, str):
        words |= _words(ident)
    classes = tag.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    for cls in classes:
        words |= _words(str(cls))
    return words


def _text_len(tag: Any) -> int:
    return len(tag.get_text(" ", strip=True))


def _link_len(tag: Any) -> int:
    return sum(len(a.get_text(" ", strip=True)) for a in tag.find_all("a"))


def _is_chrome(tag: Any) -> bool:
    role = tag.get("role")
    if isinstance(role, str) and role.lower() in _CHROME_ROLES:
        return True
    if tag.get("aria-hidden") == "true":
        return True
    return bool(_attr_words(tag) & _CHROME_WORDS)


_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_TITLE_WORDS = frozenset({"title", "heading", "headline"})


def _mark_structure(root: Any) -> None:
    """Keep the structure the model needs to pair things up.

    Headings (h1-h6, or an element whose class says "title") get a "## "
    prefix: on a guide each heading is usually an item and the text under
    it its description. List items get "- " (bulleted, typically
    ingredients) or "1. " (numbered, typically steps)."""
    for tag in root.find_all(True):
        if getattr(tag, "decomposed", False) or tag.attrs is None:
            continue
        is_heading = tag.name in _HEADING_TAGS or (
            bool(_attr_words(tag) & _TITLE_WORDS)
            and not tag.find(_HEADING_TAGS)
            and 0 < _text_len(tag) <= 150
        )
        if is_heading:
            tag.insert(0, "\n## ")
            tag.append("\n")
    for ordered in (False, True):
        for parent in root.find_all("ol" if ordered else "ul"):
            items = parent.find_all("li", recursive=False)
            for number, li in enumerate(items, start=1):
                li.insert(0, f"\n{number}. " if ordered else "\n- ")


def extract_main_text(html: str, max_chars: int = 40_000) -> str:
    """Readable main text of *html*, one block per line."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()[:max_chars]

    soup = BeautifulSoup(html, "html.parser")
    root = soup.body or soup

    for tag in root.find_all(_NEVER_CONTENT_TAGS):
        tag.decompose()

    total = max(_text_len(root), 1)

    # Chrome by role / id / class -- but never a wrapper of most content.
    for tag in list(root.find_all(True)):
        if getattr(tag, "decomposed", False) or tag.attrs is None:
            continue
        if _is_chrome(tag) and _text_len(tag) <= total * 0.5:
            tag.decompose()

    # Link farms: menus and language lists without a telling class.
    for tag in list(root.find_all(["ul", "ol", "div", "section", "table", "p"])):
        if getattr(tag, "decomposed", False) or tag.attrs is None:
            continue
        length = _text_len(tag)
        if length == 0 or length > total * 0.3:
            continue
        links = [a for a in tag.find_all("a") if a.get_text(strip=True)]
        if len(links) >= _LINK_FARM_MIN_LINKS and _link_len(tag) / length >= _LINK_FARM_DENSITY:
            tag.decompose()

    # Prefer an explicit main-content element when the page has one.
    mains = [el for sel in _MAIN_SELECTORS for el in root.select(sel)]
    mains = [el for el in mains if _text_len(el) >= _MIN_MAIN_TEXT]
    if mains:
        root = max(mains, key=_text_len)

    _mark_structure(root)
    lines = [line.strip() for line in root.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    # get_text("\n") puts a separator after the inserted prefix too; glue a
    # lone "##" / "-" / "3." back onto the line it belongs to.
    merged: list[str] = []
    pending = ""
    for line in lines:
        if line in ("##", "-") or re.fullmatch(r"\d+\.", line):
            pending = line + " "
            continue
        merged.append(pending + line)
        pending = ""
    text = "\n".join(merged)
    log.debug("[readable] %d chars of page text kept", len(text))
    return text[:max_chars]
