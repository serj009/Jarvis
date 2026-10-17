"""Template: Pygame Game."""

TEMPLATE: dict = {
    "name": "pygame_game",
    "display_name": "Pygame Game",
    "description": "Simple game with pygame",
    "keywords": [
        "game", "pygame", "игра", "гра", "gamedev",
        "game development", "2d",
    ],
    "files": {
        "game.py": (
            '"""{project_name} — main game loop."""\n'
            "\n"
            "import sys\n"
            "\n"
            "import pygame\n"
            "\n"
            "from settings import (\n"
            "    SCREEN_WIDTH, SCREEN_HEIGHT, FPS, TITLE,\n"
            "    BG_COLOR,\n"
            ")\n"
            "from sprites import Player\n"
            "\n"
            "\n"
            "class Game:\n"
            '    """Main game class."""\n'
            "\n"
            "    def __init__(self):\n"
            "        pygame.init()\n"
            "        self.screen = pygame.display.set_mode(\n"
            "            (SCREEN_WIDTH, SCREEN_HEIGHT)\n"
            "        )\n"
            "        pygame.display.set_caption(TITLE)\n"
            "        self.clock = pygame.time.Clock()\n"
            "        self.running = True\n"
            "\n"
            "        # Sprite groups\n"
            "        self.all_sprites = pygame.sprite.Group()\n"
            "        self.player = Player(\n"
            "            SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2\n"
            "        )\n"
            "        self.all_sprites.add(self.player)\n"
            "\n"
            "    def handle_events(self):\n"
            '        """Process input events."""\n'
            "        for event in pygame.event.get():\n"
            "            if event.type == pygame.QUIT:\n"
            "                self.running = False\n"
            "            elif event.type == pygame.KEYDOWN:\n"
            "                if event.key == pygame.K_ESCAPE:\n"
            "                    self.running = False\n"
            "\n"
            "    def update(self):\n"
            '        """Update game state."""\n'
            "        keys = pygame.key.get_pressed()\n"
            "        self.player.handle_input(keys)\n"
            "        self.all_sprites.update()\n"
            "\n"
            "    def draw(self):\n"
            '        """Render the frame."""\n'
            "        self.screen.fill(BG_COLOR)\n"
            "        self.all_sprites.draw(self.screen)\n"
            "        pygame.display.flip()\n"
            "\n"
            "    def run(self):\n"
            '        """Main game loop."""\n'
            "        while self.running:\n"
            "            self.clock.tick(FPS)\n"
            "            self.handle_events()\n"
            "            self.update()\n"
            "            self.draw()\n"
            "\n"
            "        pygame.quit()\n"
            "        sys.exit()\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    Game().run()\n"
        ),
        "sprites.py": (
            '"""Sprites for {project_name}."""\n'
            "\n"
            "import pygame\n"
            "\n"
            "from settings import (\n"
            "    PLAYER_SIZE, PLAYER_COLOR, PLAYER_SPEED,\n"
            "    SCREEN_WIDTH, SCREEN_HEIGHT,\n"
            ")\n"
            "\n"
            "\n"
            "class Player(pygame.sprite.Sprite):\n"
            '    """Player sprite with keyboard movement."""\n'
            "\n"
            "    def __init__(self, x: int, y: int):\n"
            "        super().__init__()\n"
            "        self.image = pygame.Surface(\n"
            "            (PLAYER_SIZE, PLAYER_SIZE)\n"
            "        )\n"
            "        self.image.fill(PLAYER_COLOR)\n"
            "        self.rect = self.image.get_rect(center=(x, y))\n"
            "        self.speed = PLAYER_SPEED\n"
            "        self.vx = 0\n"
            "        self.vy = 0\n"
            "\n"
            "    def handle_input(self, keys):\n"
            '        """Set velocity based on pressed keys."""\n'
            "        self.vx = 0\n"
            "        self.vy = 0\n"
            "        if keys[pygame.K_LEFT] or keys[pygame.K_a]:\n"
            "            self.vx = -self.speed\n"
            "        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:\n"
            "            self.vx = self.speed\n"
            "        if keys[pygame.K_UP] or keys[pygame.K_w]:\n"
            "            self.vy = -self.speed\n"
            "        if keys[pygame.K_DOWN] or keys[pygame.K_s]:\n"
            "            self.vy = self.speed\n"
            "\n"
            "    def update(self):\n"
            '        """Move the player and clamp to screen."""\n'
            "        self.rect.x += self.vx\n"
            "        self.rect.y += self.vy\n"
            "        self.rect.clamp_ip(\n"
            "            pygame.Rect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT)\n"
            "        )\n"
        ),
        "settings.py": (
            '"""Game settings for {project_name}."""\n'
            "\n"
            "# Window\n"
            'TITLE = "{project_name}"\n'
            "SCREEN_WIDTH = 800\n"
            "SCREEN_HEIGHT = 600\n"
            "FPS = 60\n"
            "\n"
            "# Colors (R, G, B)\n"
            "BG_COLOR = (30, 30, 50)\n"
            "PLAYER_COLOR = (0, 200, 100)\n"
            "\n"
            "# Player\n"
            "PLAYER_SIZE = 40\n"
            "PLAYER_SPEED = 5\n"
        ),
        "assets/.gitkeep": "",
        "tests/__init__.py": "",
        "tests/test_game.py": (
            '"""Tests for {project_name} game logic."""\n'
            "\n"
            "from settings import (\n"
            "    SCREEN_WIDTH, SCREEN_HEIGHT, PLAYER_SIZE,\n"
            "    PLAYER_SPEED, PLAYER_COLOR,\n"
            ")\n"
            "\n"
            "\n"
            "def test_settings_values():\n"
            "    assert SCREEN_WIDTH > 0\n"
            "    assert SCREEN_HEIGHT > 0\n"
            "    assert PLAYER_SIZE > 0\n"
            "    assert PLAYER_SPEED > 0\n"
            "\n"
            "\n"
            "def test_player_color_is_rgb():\n"
            "    assert len(PLAYER_COLOR) == 3\n"
            "    assert all(0 <= c <= 255 for c in PLAYER_COLOR)\n"
            "\n"
            "\n"
            "def test_screen_bigger_than_player():\n"
            "    assert SCREEN_WIDTH > PLAYER_SIZE\n"
            "    assert SCREEN_HEIGHT > PLAYER_SIZE\n"
        ),
        "requirements.txt": (
            "pygame-ce>=2.5\n"
            "pytest>=8.0\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A simple game built with Pygame.\n"
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
            "## Play\n"
            "\n"
            "```bash\n"
            "python game.py\n"
            "```\n"
            "\n"
            "### Controls\n"
            "\n"
            "| Key | Action |\n"
            "|-----|--------|\n"
            "| WASD / Arrow keys | Move |\n"
            "| ESC | Quit |\n"
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
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "A simple Pygame game"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "pygame-ce>=2.5",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
