"""Template: Node.js Basic Project."""

TEMPLATE: dict = {
    "name": "js_node_basic",
    "language": "javascript",
    "display_name": "Node.js Basic Project",
    "description": "Simple Node.js project with Jest tests and proper structure",
    "keywords": [
        "javascript", "js", "node", "nodejs", "нод",
        "джаваскрипт", "нода",
    ],
    "files": {
        "index.js": (
            '"use strict";\n'
            "\n"
            "const { greet, add } = require('./utils');\n"
            "\n"
            "/**\n"
            " * Main entry point for {project_name}.\n"
            " */\n"
            "function main() {\n"
            "  const name = process.argv[2] || 'World';\n"
            "  console.log(greet(name));\n"
            "  console.log(`2 + 3 = ${add(2, 3)}`);\n"
            "}\n"
            "\n"
            "main();\n"
        ),
        "utils.js": (
            '"use strict";\n'
            "\n"
            "/**\n"
            " * Return a greeting message.\n"
            " * @param {string} name\n"
            " * @returns {string}\n"
            " */\n"
            "function greet(name) {\n"
            "  if (!name || !name.trim()) {\n"
            "    return 'Hello, World!';\n"
            "  }\n"
            "  return `Hello, ${name.trim()}!`;\n"
            "}\n"
            "\n"
            "/**\n"
            " * Add two numbers.\n"
            " * @param {number} a\n"
            " * @param {number} b\n"
            " * @returns {number}\n"
            " */\n"
            "function add(a, b) {\n"
            "  return a + b;\n"
            "}\n"
            "\n"
            "module.exports = { greet, add };\n"
        ),
        "package.json": (
            "{\n"
            '  "name": "{project_name}",\n'
            '  "version": "1.0.0",\n'
            '  "description": "A simple Node.js project",\n'
            '  "main": "index.js",\n'
            '  "scripts": {\n'
            '    "start": "node index.js",\n'
            '    "test": "jest"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "jest": "^29.7.0"\n'
            "  }\n"
            "}\n"
        ),
        "tests/test_utils.js": (
            "const { greet, add } = require('../utils');\n"
            "\n"
            "describe('greet', () => {\n"
            "  test('greets by name', () => {\n"
            "    expect(greet('Alice')).toBe('Hello, Alice!');\n"
            "  });\n"
            "\n"
            "  test('greets world when empty', () => {\n"
            "    expect(greet('')).toBe('Hello, World!');\n"
            "  });\n"
            "});\n"
            "\n"
            "describe('add', () => {\n"
            "  test('adds two numbers', () => {\n"
            "    expect(add(2, 3)).toBe(5);\n"
            "    expect(add(-1, 1)).toBe(0);\n"
            "  });\n"
            "});\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A simple Node.js project.\n"
            "\n"
            "## Setup\n"
            "\n"
            "```bash\n"
            "npm install\n"
            "```\n"
            "\n"
            "## Run\n"
            "\n"
            "```bash\n"
            "npm start\n"
            "```\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "npm test\n"
            "```\n"
        ),
        ".gitignore": (
            "node_modules/\n"
            "dist/\n"
            "build/\n"
            "coverage/\n"
            ".env\n"
            ".env.local\n"
            "*.log\n"
            "npm-debug.log*\n"
            ".DS_Store\n"
            "Thumbs.db\n"
            ".idea/\n"
            ".vscode/\n"
            "*.swp\n"
        ),
    },
    "post_create_commands": ["npm install"],
    "test_command": "npm test",
}
