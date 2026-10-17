"""Template: Flask REST API."""

TEMPLATE: dict = {
    "name": "flask_api",
    "display_name": "Flask REST API",
    "description": "REST API with Flask, SQLAlchemy, and pytest",
    "keywords": [
        "flask", "api", "rest", "web", "server",
        "сервер", "апі", "апи", "веб",
    ],
    "files": {
        "app/__init__.py": (
            '"""Flask application factory for {project_name}."""\n'
            "\n"
            "from flask import Flask\n"
            "\n"
            "from app.routes import api_bp\n"
            "\n"
            "\n"
            "def create_app(config: dict | None = None) -> Flask:\n"
            '    """Create and configure the Flask application."""\n'
            "    app = Flask(__name__)\n"
            "\n"
            "    # Default configuration\n"
            '    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"\n'
            '    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False\n'
            '    app.config["TESTING"] = False\n'
            "\n"
            "    if config:\n"
            "        app.config.update(config)\n"
            "\n"
            "    # Initialize extensions\n"
            "    from app.models import db\n"
            "    db.init_app(app)\n"
            "\n"
            "    with app.app_context():\n"
            "        db.create_all()\n"
            "\n"
            "    # Register blueprints\n"
            "    app.register_blueprint(api_bp)\n"
            "\n"
            "    return app\n"
        ),
        "app/models.py": (
            '"""Database models for {project_name}."""\n'
            "\n"
            "from flask_sqlalchemy import SQLAlchemy\n"
            "\n"
            "db = SQLAlchemy()\n"
            "\n"
            "\n"
            "class Item(db.Model):  # type: ignore[name-defined]\n"
            '    """Example Item model."""\n'
            "\n"
            '    __tablename__ = "items"\n'
            "\n"
            "    id = db.Column(db.Integer, primary_key=True)\n"
            "    name = db.Column(db.String(128), nullable=False)\n"
            "    description = db.Column(db.Text, default=\"\")\n"
            "    created_at = db.Column(\n"
            "        db.DateTime, server_default=db.func.now()\n"
            "    )\n"
            "\n"
            "    def to_dict(self) -> dict:\n"
            '        """Serialize item to dictionary."""\n'
            "        return {\n"
            '            "id": self.id,\n'
            '            "name": self.name,\n'
            '            "description": self.description,\n'
            '            "created_at": (\n'
            "                self.created_at.isoformat()\n"
            "                if self.created_at else None\n"
            "            ),\n"
            "        }\n"
        ),
        "app/routes.py": (
            '"""API routes for {project_name}."""\n'
            "\n"
            "from flask import Blueprint, jsonify, request\n"
            "\n"
            "from app.models import db, Item\n"
            "\n"
            'api_bp = Blueprint("api", __name__, url_prefix="/api")\n'
            "\n"
            "\n"
            '@api_bp.route("/health")\n'
            "def health():\n"
            '    """Health check endpoint."""\n'
            '    return jsonify({"status": "ok"})\n'
            "\n"
            "\n"
            '@api_bp.route("/items", methods=["GET"])\n'
            "def list_items():\n"
            '    """List all items."""\n'
            "    items = Item.query.all()\n"
            '    return jsonify({"items": [i.to_dict() for i in items]})\n'
            "\n"
            "\n"
            '@api_bp.route("/items", methods=["POST"])\n'
            "def create_item():\n"
            '    """Create a new item."""\n'
            "    data = request.get_json()\n"
            '    if not data or "name" not in data:\n'
            "        return jsonify(\n"
            '            {"error": "name is required"}\n'
            "        ), 400\n"
            "\n"
            "    item = Item(\n"
            '        name=data["name"],\n'
            '        description=data.get("description", ""),\n'
            "    )\n"
            "    db.session.add(item)\n"
            "    db.session.commit()\n"
            "    return jsonify(item.to_dict()), 201\n"
            "\n"
            "\n"
            '@api_bp.route("/items/<int:item_id>", methods=["GET"])\n'
            "def get_item(item_id: int):\n"
            '    """Get item by ID."""\n'
            "    item = db.session.get(Item, item_id)\n"
            "    if not item:\n"
            '        return jsonify({"error": "not found"}), 404\n'
            "    return jsonify(item.to_dict())\n"
            "\n"
            "\n"
            '@api_bp.route("/items/<int:item_id>", methods=["DELETE"])\n'
            "def delete_item(item_id: int):\n"
            '    """Delete item by ID."""\n'
            "    item = db.session.get(Item, item_id)\n"
            "    if not item:\n"
            '        return jsonify({"error": "not found"}), 404\n'
            "    db.session.delete(item)\n"
            "    db.session.commit()\n"
            '    return jsonify({"deleted": item_id})\n'
        ),
        "config.py": (
            '"""Configuration for {project_name}."""\n'
            "\n"
            "import os\n"
            "\n"
            "\n"
            "class Config:\n"
            '    """Base configuration."""\n'
            "\n"
            "    SECRET_KEY = os.environ.get(\n"
            '        "SECRET_KEY", "dev-secret-change-me"\n'
            "    )\n"
            "    SQLALCHEMY_DATABASE_URI = os.environ.get(\n"
            '        "DATABASE_URL", "sqlite:///app.db"\n'
            "    )\n"
            "    SQLALCHEMY_TRACK_MODIFICATIONS = False\n"
            "\n"
            "\n"
            "class TestConfig(Config):\n"
            '    """Testing configuration."""\n'
            "\n"
            "    TESTING = True\n"
            '    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"\n'
        ),
        "run.py": (
            '"""Run the {project_name} development server."""\n'
            "\n"
            "from app import create_app\n"
            "\n"
            "app = create_app()\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    app.run(debug=True, port=5000)\n"
        ),
        "tests/__init__.py": "",
        "tests/conftest.py": (
            '"""Pytest fixtures for {project_name}."""\n'
            "\n"
            "import pytest\n"
            "\n"
            "from app import create_app\n"
            "from app.models import db as _db\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def app():\n"
            '    """Create application for testing."""\n'
            "    app = create_app({\n"
            '        "TESTING": True,\n'
            '        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",\n'
            "    })\n"
            "    with app.app_context():\n"
            "        _db.create_all()\n"
            "        yield app\n"
            "        _db.drop_all()\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def client(app):\n"
            '    """Test client."""\n'
            "    return app.test_client()\n"
        ),
        "tests/test_routes.py": (
            '"""Tests for API routes."""\n'
            "\n"
            "\n"
            "def test_health(client):\n"
            '    resp = client.get("/api/health")\n'
            "    assert resp.status_code == 200\n"
            '    assert resp.get_json()["status"] == "ok"\n'
            "\n"
            "\n"
            "def test_create_item(client):\n"
            "    resp = client.post(\n"
            '        "/api/items",\n'
            '        json={"name": "Test Item", "description": "A test"},\n'
            "    )\n"
            "    assert resp.status_code == 201\n"
            "    data = resp.get_json()\n"
            '    assert data["name"] == "Test Item"\n'
            '    assert "id" in data\n'
            "\n"
            "\n"
            "def test_create_item_missing_name(client):\n"
            '    resp = client.post("/api/items", json={})\n'
            "    assert resp.status_code == 400\n"
            "\n"
            "\n"
            "def test_list_items(client):\n"
            '    client.post("/api/items", json={"name": "A"})\n'
            '    client.post("/api/items", json={"name": "B"})\n'
            '    resp = client.get("/api/items")\n'
            "    assert resp.status_code == 200\n"
            '    assert len(resp.get_json()["items"]) == 2\n'
            "\n"
            "\n"
            "def test_get_item_not_found(client):\n"
            '    resp = client.get("/api/items/999")\n'
            "    assert resp.status_code == 404\n"
        ),
        "requirements.txt": (
            "flask>=3.0\n"
            "flask-sqlalchemy>=3.1\n"
            "pytest>=8.0\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A Flask REST API with SQLAlchemy.\n"
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
            "python run.py\n"
            "```\n"
            "\n"
            "The API will be available at http://localhost:5000\n"
            "\n"
            "## API Endpoints\n"
            "\n"
            "| Method | Endpoint | Description |\n"
            "|--------|----------|-------------|\n"
            "| GET | /api/health | Health check |\n"
            "| GET | /api/items | List all items |\n"
            "| POST | /api/items | Create item |\n"
            "| GET | /api/items/:id | Get item |\n"
            "| DELETE | /api/items/:id | Delete item |\n"
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
            "instance/\n"
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "Flask REST API"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "flask>=3.0",\n'
            '    "flask-sqlalchemy>=3.1",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
