"""Template: FastAPI Async API."""

TEMPLATE: dict = {
    "name": "fastapi",
    "display_name": "FastAPI Async API",
    "description": "Async REST API with FastAPI, Pydantic, and pytest",
    "keywords": [
        "fastapi", "async", "api", "rest", "асинхронный",
        "fast", "pydantic",
    ],
    "files": {
        "app/__init__.py": '"""FastAPI application — {project_name}."""\n',
        "app/main.py": (
            '"""FastAPI entry point for {project_name}."""\n'
            "\n"
            "from fastapi import FastAPI\n"
            "\n"
            "from app.routes import router\n"
            "\n"
            'app = FastAPI(title="{project_name}", version="0.1.0")\n'
            "app.include_router(router)\n"
            "\n"
            "\n"
            '@app.get("/health")\n'
            "async def health():\n"
            '    """Health check."""\n'
            '    return {"status": "ok"}\n'
        ),
        "app/schemas.py": (
            '"""Pydantic schemas for {project_name}."""\n'
            "\n"
            "from pydantic import BaseModel, Field\n"
            "\n"
            "\n"
            "class ItemCreate(BaseModel):\n"
            '    """Schema for creating an item."""\n'
            "\n"
            "    name: str = Field(..., min_length=1, max_length=128)\n"
            '    description: str = Field(default="")\n'
            "\n"
            "\n"
            "class ItemResponse(BaseModel):\n"
            '    """Schema for item response."""\n'
            "\n"
            "    id: int\n"
            "    name: str\n"
            "    description: str\n"
            "\n"
            "    model_config = {\"from_attributes\": True}\n"
        ),
        "app/models.py": (
            '"""Data models for {project_name}."""\n'
            "\n"
            "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "# In-memory store for simplicity — replace with a real DB.\n"
            "_items: dict[int, \"Item\"] = {}\n"
            "_next_id: int = 1\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Item:\n"
            '    """Item data model."""\n'
            "\n"
            "    id: int\n"
            "    name: str\n"
            '    description: str = ""\n'
            "\n"
            "\n"
            "def create_item(name: str, description: str = \"\") -> Item:\n"
            '    """Create and store a new item."""\n'
            "    global _next_id\n"
            "    item = Item(id=_next_id, name=name, description=description)\n"
            "    _items[_next_id] = item\n"
            "    _next_id += 1\n"
            "    return item\n"
            "\n"
            "\n"
            "def get_item(item_id: int) -> Item | None:\n"
            '    """Get item by ID."""\n'
            "    return _items.get(item_id)\n"
            "\n"
            "\n"
            "def list_items() -> list[Item]:\n"
            '    """List all items."""\n'
            "    return list(_items.values())\n"
            "\n"
            "\n"
            "def delete_item(item_id: int) -> bool:\n"
            '    """Delete item by ID. Returns True if deleted."""\n'
            "    return _items.pop(item_id, None) is not None\n"
            "\n"
            "\n"
            "def clear_items() -> None:\n"
            '    """Clear all items (useful for tests)."""\n'
            "    global _next_id\n"
            "    _items.clear()\n"
            "    _next_id = 1\n"
        ),
        "app/routes.py": (
            '"""API routes for {project_name}."""\n'
            "\n"
            "from fastapi import APIRouter, HTTPException\n"
            "\n"
            "from app.schemas import ItemCreate, ItemResponse\n"
            "from app import models\n"
            "\n"
            'router = APIRouter(prefix="/api")\n'
            "\n"
            "\n"
            '@router.get("/items", response_model=list[ItemResponse])\n'
            "async def list_items():\n"
            '    """List all items."""\n'
            "    return models.list_items()\n"
            "\n"
            "\n"
            '@router.post("/items", response_model=ItemResponse, status_code=201)\n'
            "async def create_item(payload: ItemCreate):\n"
            '    """Create a new item."""\n'
            "    return models.create_item(\n"
            "        name=payload.name, description=payload.description\n"
            "    )\n"
            "\n"
            "\n"
            '@router.get("/items/{item_id}", response_model=ItemResponse)\n'
            "async def get_item(item_id: int):\n"
            '    """Get item by ID."""\n'
            "    item = models.get_item(item_id)\n"
            "    if not item:\n"
            '        raise HTTPException(status_code=404, detail="not found")\n'
            "    return item\n"
            "\n"
            "\n"
            '@router.delete("/items/{item_id}")\n'
            "async def delete_item(item_id: int):\n"
            '    """Delete item by ID."""\n'
            "    if not models.delete_item(item_id):\n"
            '        raise HTTPException(status_code=404, detail="not found")\n'
            '    return {"deleted": item_id}\n'
        ),
        "tests/__init__.py": "",
        "tests/test_api.py": (
            '"""Tests for {project_name} API."""\n'
            "\n"
            "import pytest\n"
            "from fastapi.testclient import TestClient\n"
            "\n"
            "from app.main import app\n"
            "from app import models\n"
            "\n"
            "\n"
            "@pytest.fixture(autouse=True)\n"
            "def _reset_store():\n"
            '    """Reset in-memory store between tests."""\n'
            "    models.clear_items()\n"
            "    yield\n"
            "    models.clear_items()\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def client():\n"
            "    return TestClient(app)\n"
            "\n"
            "\n"
            "def test_health(client):\n"
            '    resp = client.get("/health")\n'
            "    assert resp.status_code == 200\n"
            '    assert resp.json()["status"] == "ok"\n'
            "\n"
            "\n"
            "def test_create_item(client):\n"
            "    resp = client.post(\n"
            '        "/api/items",\n'
            '        json={"name": "Widget", "description": "A widget"},\n'
            "    )\n"
            "    assert resp.status_code == 201\n"
            "    data = resp.json()\n"
            '    assert data["name"] == "Widget"\n'
            '    assert "id" in data\n'
            "\n"
            "\n"
            "def test_list_items(client):\n"
            '    client.post("/api/items", json={"name": "A"})\n'
            '    client.post("/api/items", json={"name": "B"})\n'
            '    resp = client.get("/api/items")\n'
            "    assert resp.status_code == 200\n"
            "    assert len(resp.json()) == 2\n"
            "\n"
            "\n"
            "def test_get_item_not_found(client):\n"
            '    resp = client.get("/api/items/999")\n'
            "    assert resp.status_code == 404\n"
        ),
        "requirements.txt": (
            "fastapi>=0.115.0\n"
            "uvicorn[standard]>=0.30.0\n"
            "pydantic>=2.9\n"
            "pytest>=8.0\n"
            "httpx>=0.27\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "An async REST API built with FastAPI.\n"
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
            "## Run\n"
            "\n"
            "```bash\n"
            "uvicorn app.main:app --reload --port 8000\n"
            "```\n"
            "\n"
            "API docs: http://localhost:8000/docs\n"
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
            "*.so\n"
            "build/\n"
            "dist/\n"
            "*.egg-info/\n"
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
            "*.db\n"
            "*.sqlite3\n"
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "FastAPI async REST API"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "fastapi>=0.115.0",\n'
            '    "uvicorn[standard]>=0.30.0",\n'
            '    "pydantic>=2.9",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0", "httpx>=0.27"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
