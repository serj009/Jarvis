"""Project scaffolding — pre-built templates for common project types.

When CodingAgent receives "create a Flask API" or "создай бота для Telegram",
the scaffolder provides the complete file structure and boilerplate code.
The coding model then only needs to fill in the business logic, not figure
out the project layout from scratch.

This dramatically improves output quality for small models (7-8B) because
they don't have to "invent" a proper project structure.

Usage::

    from jarvis.coding.scaffolder import find_template, scaffold_project
    from pathlib import Path

    tpl = find_template("create a Flask REST API")
    if tpl:
        files = scaffold_project(tpl, "my_api", Path("./my_api"))
        print(f"Created {len(files)} files")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jarvis.coding.templates import TEMPLATES

logger = logging.getLogger(__name__)

# Required keys every template dict must have
REQUIRED_TEMPLATE_KEYS = frozenset({
    "name",
    "display_name",
    "description",
    "keywords",
    "files",
    "post_create_commands",
    "test_command",
})


def find_template(description: str) -> dict | None:
    """Find the best matching template by keywords in *description*.

    Performs case-insensitive keyword matching.  Returns the template
    with the most keyword hits, or ``None`` when nothing matches.

    Args:
        description: User request text, e.g. "create a telegram bot".

    Returns:
        Template dict or ``None``.
    """
    if not description:
        return None

    description_lower = description.lower()

    best_template: dict | None = None
    best_score = 0

    for tpl in TEMPLATES:
        score = 0
        for kw in tpl["keywords"]:
            if kw.lower() in description_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_template = tpl

    return best_template


def scaffold_project(
    template: dict,
    project_name: str,
    target_dir: Path,
) -> list[str]:
    """Create all template files on disk.

    The ``{project_name}`` placeholder inside file contents (and in
    ``config.yaml`` / ``pyproject.toml``) is replaced with the given
    *project_name* value.

    Args:
        template:     A template dict (from :data:`TEMPLATES`).
        project_name: The project name to inject into placeholders.
        target_dir:   Root directory where files will be written.

    Returns:
        Sorted list of absolute paths to every created file.
    """
    created: list[str] = []
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    for rel_path, content in template["files"].items():
        file_path = target_dir / rel_path

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Replace {project_name} placeholder
        rendered = content.replace("{project_name}", project_name)

        file_path.write_text(rendered, encoding="utf-8")
        created.append(str(file_path.resolve()))
        logger.debug("Created: %s", file_path)

    logger.info(
        "Scaffolded '%s' (%s): %d files in %s",
        template["name"],
        project_name,
        len(created),
        target_dir,
    )
    return sorted(created)


def list_templates() -> list[dict]:
    """Return summaries of all available templates.

    Each summary contains ``name``, ``display_name``, ``description``,
    and ``keywords`` (without the full file contents).
    """
    return [
        {
            "name": t["name"],
            "display_name": t["display_name"],
            "description": t["description"],
            "keywords": t["keywords"],
        }
        for t in TEMPLATES
    ]


def get_template(name: str) -> dict | None:
    """Get a template by its exact *name* slug.

    Args:
        name: Template slug, e.g. ``"flask_api"``.

    Returns:
        Template dict or ``None``.
    """
    for tpl in TEMPLATES:
        if tpl["name"] == name:
            return tpl
    return None
