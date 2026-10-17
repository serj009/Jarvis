"""Template: Web Scraper."""

TEMPLATE: dict = {
    "name": "scraper",
    "display_name": "Web Scraper",
    "description": "Web scraper with requests, BeautifulSoup, and data export",
    "keywords": [
        "scraper", "parser", "парсер", "скрейпер", "парсинг", "сайт",
        "crawl", "scraping", "web scraper", "beautifulsoup", "краулер",
    ],
    "files": {
        "scraper.py": (
            '"""{project_name} — web scraper entry point."""\n'
            "\n"
            "import argparse\n"
            "import json\n"
            "import logging\n"
            "\n"
            "from parser import PageParser\n"
            "from models import ScrapedItem\n"
            "\n"
            "logging.basicConfig(\n"
            '    format="%(asctime)s - %(levelname)s - %(message)s",\n'
            "    level=logging.INFO,\n"
            ")\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "\n"
            "def scrape(url: str) -> list[ScrapedItem]:\n"
            '    """Scrape a URL and return parsed items."""\n'
            "    import requests\n"
            "\n"
            '    logger.info(f"Scraping {{url}}")\n'
            "    response = requests.get(url, timeout=30)\n"
            "    response.raise_for_status()\n"
            "\n"
            "    parser = PageParser()\n"
            "    items = parser.parse(response.text, base_url=url)\n"
            '    logger.info(f"Found {{len(items)}} items")\n'
            "    return items\n"
            "\n"
            "\n"
            "def save_results(\n"
            "    items: list[ScrapedItem], output: str\n"
            ") -> None:\n"
            '    """Save scraped items to JSON file."""\n'
            "    data = [item.to_dict() for item in items]\n"
            '    with open(output, "w", encoding="utf-8") as f:\n'
            "        json.dump(data, f, ensure_ascii=False, indent=2)\n"
            '    logger.info(f"Saved {{len(data)}} items to {{output}}")\n'
            "\n"
            "\n"
            "def main():\n"
            '    """CLI entry point."""\n'
            "    ap = argparse.ArgumentParser(\n"
            '        description="{project_name} web scraper"\n'
            "    )\n"
            '    ap.add_argument("url", help="URL to scrape")\n'
            "    ap.add_argument(\n"
            '        "-o", "--output", default="results.json",\n'
            '        help="Output file (default: results.json)",\n'
            "    )\n"
            "    args = ap.parse_args()\n"
            "\n"
            "    items = scrape(args.url)\n"
            "    save_results(items, args.output)\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "parser.py": (
            '"""HTML parser for {project_name}."""\n'
            "\n"
            "from urllib.parse import urljoin\n"
            "\n"
            "from bs4 import BeautifulSoup\n"
            "\n"
            "from models import ScrapedItem\n"
            "\n"
            "\n"
            "class PageParser:\n"
            '    """Parse HTML pages and extract structured data."""\n'
            "\n"
            "    def parse(\n"
            "        self, html: str, base_url: str = \"\"\n"
            "    ) -> list[ScrapedItem]:\n"
            '        """Parse HTML and return list of scraped items."""\n'
            '        soup = BeautifulSoup(html, "html.parser")\n'
            "        items: list[ScrapedItem] = []\n"
            "\n"
            "        # Extract all links with text\n"
            '        for link in soup.find_all("a", href=True):\n'
            "            text = link.get_text(strip=True)\n"
            "            if not text:\n"
            "                continue\n"
            '            href = link["href"]\n'
            "            if base_url:\n"
            "                href = urljoin(base_url, href)\n"
            "            items.append(\n"
            "                ScrapedItem(title=text, url=href)\n"
            "            )\n"
            "\n"
            "        return items\n"
            "\n"
            "    def extract_text(self, html: str) -> str:\n"
            '        """Extract all visible text from HTML."""\n'
            '        soup = BeautifulSoup(html, "html.parser")\n'
            '        for tag in soup(["script", "style", "nav", "footer"]):\n'
            "            tag.decompose()\n"
            '        return soup.get_text("\\n", strip=True)\n'
        ),
        "models.py": (
            '"""Data models for {project_name}."""\n'
            "\n"
            "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class ScrapedItem:\n"
            '    """A single scraped item."""\n'
            "\n"
            "    title: str\n"
            '    url: str = ""\n'
            '    description: str = ""\n'
            "    extra: dict = field(default_factory=dict)\n"
            "\n"
            "    def to_dict(self) -> dict:\n"
            '        """Convert to dictionary."""\n'
            "        return {\n"
            '            "title": self.title,\n'
            '            "url": self.url,\n'
            '            "description": self.description,\n'
            '            "extra": self.extra,\n'
            "        }\n"
        ),
        "config.yaml": (
            "# {project_name} configuration\n"
            "\n"
            "scraper:\n"
            "  timeout: 30\n"
            "  max_retries: 3\n"
            "  delay_between_requests: 1.0  # seconds\n"
            "  user_agent: \"{project_name}/0.1\"\n"
            "\n"
            "output:\n"
            "  format: json\n"
            "  directory: ./results\n"
        ),
        "tests/__init__.py": "",
        "tests/test_parser.py": (
            '"""Tests for {project_name} parser."""\n'
            "\n"
            "from parser import PageParser\n"
            "\n"
            "\n"
            "SAMPLE_HTML = \"\"\"\n"
            "<html>\n"
            "<body>\n"
            "  <a href=\"/page1\">Page One</a>\n"
            "  <a href=\"https://example.com/page2\">Page Two</a>\n"
            "  <a href=\"#\"></a>\n"
            "  <p>Some paragraph text.</p>\n"
            "</body>\n"
            "</html>\n"
            "\"\"\"\n"
            "\n"
            "\n"
            "def test_parse_extracts_links():\n"
            "    p = PageParser()\n"
            "    items = p.parse(SAMPLE_HTML, base_url=\"https://example.com\")\n"
            "    assert len(items) == 2\n"
            "    assert items[0].title == \"Page One\"\n"
            "    assert items[0].url == \"https://example.com/page1\"\n"
            "    assert items[1].title == \"Page Two\"\n"
            "\n"
            "\n"
            "def test_parse_skips_empty_links():\n"
            "    p = PageParser()\n"
            "    items = p.parse(SAMPLE_HTML)\n"
            "    titles = [i.title for i in items]\n"
            "    assert all(t for t in titles)  # no empty titles\n"
            "\n"
            "\n"
            "def test_extract_text():\n"
            "    p = PageParser()\n"
            "    text = p.extract_text(SAMPLE_HTML)\n"
            "    assert \"Some paragraph text.\" in text\n"
            "\n"
            "\n"
            "def test_scraped_item_to_dict():\n"
            "    from models import ScrapedItem\n"
            "\n"
            "    item = ScrapedItem(\n"
            "        title=\"Test\", url=\"https://example.com\"\n"
            "    )\n"
            "    d = item.to_dict()\n"
            "    assert d[\"title\"] == \"Test\"\n"
            "    assert d[\"url\"] == \"https://example.com\"\n"
        ),
        "requirements.txt": (
            "requests>=2.32\n"
            "beautifulsoup4>=4.12\n"
            "pyyaml>=6.0\n"
            "pytest>=8.0\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A web scraper built with Requests and BeautifulSoup.\n"
            "\n"
            "## Setup\n"
            "\n"
            "```bash\n"
            "python -m venv .venv\n"
            "source .venv/bin/activate  # Linux/macOS\n"
            ".venv\\\\Scripts\\\\activate     # Windows\n"
            "pip install -r requirements.txt\n"
            "```\n"
            "\n"
            "## Usage\n"
            "\n"
            "```bash\n"
            "python scraper.py https://example.com -o results.json\n"
            "```\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "python -m pytest -q\n"
            "```\n"
        ),
        ".gitignore": (
            "__pycache__/\n"
            "*.py[cod]\n"
            "*$py.class\n"
            ".env\n"
            ".venv\n"
            "env/\n"
            "venv/\n"
            ".idea/\n"
            ".vscode/\n"
            "*.swp\n"
            ".DS_Store\n"
            "Thumbs.db\n"
            ".pytest_cache/\n"
            ".coverage\n"
            "results/\n"
            "*.json\n"
            "!config.yaml\n"
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "Web scraper"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "requests>=2.32",\n'
            '    "beautifulsoup4>=4.12",\n'
            '    "pyyaml>=6.0",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
