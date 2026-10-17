"""Template: React TypeScript Application."""

TEMPLATE: dict = {
    "name": "react_app",
    "language": "typescript",
    "display_name": "React TypeScript App",
    "description": "React application with TypeScript and Jest tests",
    "keywords": [
        "react", "реакт", "фронтенд", "frontend", "web app",
        "веб-приложение", "вебдодаток", "spa",
    ],
    "files": {
        "src/App.tsx": (
            "import React, { useState } from 'react';\n"
            "\n"
            "/**\n"
            " * Main application component for {project_name}.\n"
            " */\n"
            "const App: React.FC = () => {\n"
            "  const [count, setCount] = useState<number>(0);\n"
            "\n"
            "  return (\n"
            "    <div className=\"App\">\n"
            "      <h1>{project_name}</h1>\n"
            "      <p>Count: {count}</p>\n"
            "      <button onClick={() => setCount(c => c + 1)}>Increment</button>\n"
            "      <button onClick={() => setCount(0)}>Reset</button>\n"
            "    </div>\n"
            "  );\n"
            "};\n"
            "\n"
            "export default App;\n"
        ),
        "src/index.tsx": (
            "import React from 'react';\n"
            "import ReactDOM from 'react-dom/client';\n"
            "import App from './App';\n"
            "\n"
            "const root = ReactDOM.createRoot(\n"
            "  document.getElementById('root') as HTMLElement\n"
            ");\n"
            "\n"
            "root.render(\n"
            "  <React.StrictMode>\n"
            "    <App />\n"
            "  </React.StrictMode>\n"
            ");\n"
        ),
        "public/index.html": (
            "<!DOCTYPE html>\n"
            "<html lang=\"en\">\n"
            "<head>\n"
            "  <meta charset=\"UTF-8\" />\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
            "  <title>{project_name}</title>\n"
            "</head>\n"
            "<body>\n"
            "  <div id=\"root\"></div>\n"
            "</body>\n"
            "</html>\n"
        ),
        "tsconfig.json": (
            "{\n"
            '  "compilerOptions": {\n'
            '    "target": "ES2020",\n'
            '    "lib": ["DOM", "DOM.Iterable", "ES2020"],\n'
            '    "module": "ESNext",\n'
            '    "moduleResolution": "bundler",\n'
            '    "jsx": "react-jsx",\n'
            '    "strict": true,\n'
            '    "esModuleInterop": true,\n'
            '    "skipLibCheck": true,\n'
            '    "forceConsistentCasingInFileNames": true,\n'
            '    "outDir": "./dist"\n'
            "  },\n"
            '  "include": ["src/**/*"],\n'
            '  "exclude": ["node_modules", "dist"]\n'
            "}\n"
        ),
        "package.json": (
            "{\n"
            '  "name": "{project_name}",\n'
            '  "version": "1.0.0",\n'
            '  "description": "A React TypeScript application",\n'
            '  "private": true,\n'
            '  "scripts": {\n'
            '    "start": "react-scripts start",\n'
            '    "build": "react-scripts build",\n'
            '    "test": "react-scripts test --watchAll=false"\n'
            "  },\n"
            '  "dependencies": {\n'
            '    "react": "^18.2.0",\n'
            '    "react-dom": "^18.2.0",\n'
            '    "react-scripts": "5.0.1",\n'
            '    "typescript": "^5.3.0"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "@types/react": "^18.2.0",\n'
            '    "@types/react-dom": "^18.2.0",\n'
            '    "@testing-library/react": "^14.0.0",\n'
            '    "@testing-library/jest-dom": "^6.0.0"\n'
            "  }\n"
            "}\n"
        ),
        "tests/test_App.tsx": (
            "import React from 'react';\n"
            "import { render, screen, fireEvent } from '@testing-library/react';\n"
            "import App from '../src/App';\n"
            "\n"
            "describe('App', () => {\n"
            "  test('renders heading', () => {\n"
            "    render(<App />);\n"
            "    expect(screen.getByText('{project_name}')).toBeInTheDocument();\n"
            "  });\n"
            "\n"
            "  test('increments counter on click', () => {\n"
            "    render(<App />);\n"
            "    const button = screen.getByText('Increment');\n"
            "    fireEvent.click(button);\n"
            "    expect(screen.getByText('Count: 1')).toBeInTheDocument();\n"
            "  });\n"
            "\n"
            "  test('resets counter', () => {\n"
            "    render(<App />);\n"
            "    fireEvent.click(screen.getByText('Increment'));\n"
            "    fireEvent.click(screen.getByText('Reset'));\n"
            "    expect(screen.getByText('Count: 0')).toBeInTheDocument();\n"
            "  });\n"
            "});\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A React TypeScript application.\n"
            "\n"
            "## Setup\n"
            "\n"
            "```bash\n"
            "npm install\n"
            "```\n"
            "\n"
            "## Development\n"
            "\n"
            "```bash\n"
            "npm start\n"
            "```\n"
            "\n"
            "## Build\n"
            "\n"
            "```bash\n"
            "npm run build\n"
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
