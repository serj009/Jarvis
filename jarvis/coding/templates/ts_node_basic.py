"""Template: TypeScript Node.js Project."""

TEMPLATE: dict = {
    "name": "ts_node_basic",
    "language": "typescript",
    "display_name": "TypeScript Node.js Project",
    "description": "TypeScript Node.js project with Jest and ts-jest",
    "keywords": [
        "typescript", "ts", "тайпскрипт",
    ],
    "files": {
        "src/index.ts": (
            "import { greet, add } from './utils';\n"
            "\n"
            "/**\n"
            " * Main entry point for {project_name}.\n"
            " */\n"
            "function main(): void {\n"
            "  const name = process.argv[2] || 'World';\n"
            "  console.log(greet(name));\n"
            "  console.log(`2 + 3 = ${add(2, 3)}`);\n"
            "}\n"
            "\n"
            "main();\n"
        ),
        "src/utils.ts": (
            "/**\n"
            " * Return a greeting message.\n"
            " */\n"
            "export function greet(name: string): string {\n"
            "  if (!name || !name.trim()) {\n"
            "    return 'Hello, World!';\n"
            "  }\n"
            "  return `Hello, ${name.trim()}!`;\n"
            "}\n"
            "\n"
            "/**\n"
            " * Add two numbers.\n"
            " */\n"
            "export function add(a: number, b: number): number {\n"
            "  return a + b;\n"
            "}\n"
        ),
        "tsconfig.json": (
            "{\n"
            '  "compilerOptions": {\n'
            '    "target": "ES2020",\n'
            '    "module": "commonjs",\n'
            '    "lib": ["ES2020"],\n'
            '    "outDir": "./dist",\n'
            '    "rootDir": "./src",\n'
            '    "strict": true,\n'
            '    "esModuleInterop": true,\n'
            '    "skipLibCheck": true,\n'
            '    "forceConsistentCasingInFileNames": true,\n'
            '    "resolveJsonModule": true,\n'
            '    "declaration": true,\n'
            '    "sourceMap": true\n'
            "  },\n"
            '  "include": ["src/**/*"],\n'
            '  "exclude": ["node_modules", "dist", "tests"]\n'
            "}\n"
        ),
        "package.json": (
            "{\n"
            '  "name": "{project_name}",\n'
            '  "version": "1.0.0",\n'
            '  "description": "A TypeScript Node.js project",\n'
            '  "main": "dist/index.js",\n'
            '  "scripts": {\n'
            '    "build": "tsc",\n'
            '    "start": "ts-node src/index.ts",\n'
            '    "test": "jest"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "typescript": "^5.3.0",\n'
            '    "ts-node": "^10.9.0",\n'
            '    "@types/node": "^20.0.0",\n'
            '    "jest": "^29.7.0",\n'
            '    "ts-jest": "^29.1.0",\n'
            '    "@types/jest": "^29.5.0"\n'
            "  }\n"
            "}\n"
        ),
        "jest.config.js": (
            "/** @type {import('ts-jest').JestConfigWithTsJest} */\n"
            "module.exports = {\n"
            "  preset: 'ts-jest',\n"
            "  testEnvironment: 'node',\n"
            "  testMatch: ['**/tests/**/test_*.ts'],\n"
            "};\n"
        ),
        "tests/test_utils.ts": (
            "import { greet, add } from '../src/utils';\n"
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
            "A TypeScript Node.js project.\n"
            "\n"
            "## Setup\n"
            "\n"
            "```bash\n"
            "npm install\n"
            "```\n"
            "\n"
            "## Build\n"
            "\n"
            "```bash\n"
            "npm run build\n"
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
            "*.js.map\n"
        ),
    },
    "post_create_commands": ["npm install"],
    "test_command": "npm test",
}
