"""Template: Rust Basic Project."""

TEMPLATE: dict = {
    "name": "rust_basic",
    "language": "rust",
    "display_name": "Rust Basic Project",
    "description": "Simple Rust project with Cargo and built-in tests",
    "keywords": [
        "rust", "раст", "cargo", "карго",
    ],
    "files": {
        "src/main.rs": (
            "mod utils;\n"
            "\n"
            "use std::env;\n"
            "use utils::{greet, add};\n"
            "\n"
            "/// Main entry point for {project_name}.\n"
            "fn main() {\n"
            "    let name = env::args().nth(1).unwrap_or_else(|| \"World\".to_string());\n"
            "    println!(\"{}\", greet(&name));\n"
            "    println!(\"2 + 3 = {}\", add(2, 3));\n"
            "}\n"
        ),
        "src/utils.rs": (
            "/// Return a greeting message.\n"
            "pub fn greet(name: &str) -> String {\n"
            "    let trimmed = name.trim();\n"
            "    if trimmed.is_empty() {\n"
            "        \"Hello, World!\".to_string()\n"
            "    } else {\n"
            "        format!(\"Hello, {}!\", trimmed)\n"
            "    }\n"
            "}\n"
            "\n"
            "/// Add two numbers.\n"
            "pub fn add(a: i64, b: i64) -> i64 {\n"
            "    a + b\n"
            "}\n"
            "\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    use super::*;\n"
            "\n"
            "    #[test]\n"
            "    fn test_greet_with_name() {\n"
            "        assert_eq!(greet(\"Alice\"), \"Hello, Alice!\");\n"
            "    }\n"
            "\n"
            "    #[test]\n"
            "    fn test_greet_empty() {\n"
            "        assert_eq!(greet(\"\"), \"Hello, World!\");\n"
            "    }\n"
            "\n"
            "    #[test]\n"
            "    fn test_add() {\n"
            "        assert_eq!(add(2, 3), 5);\n"
            "        assert_eq!(add(-1, 1), 0);\n"
            "    }\n"
            "}\n"
        ),
        "Cargo.toml": (
            "[package]\n"
            "name = \"{project_name}\"\n"
            "version = \"0.1.0\"\n"
            "edition = \"2021\"\n"
            "\n"
            "[dependencies]\n"
        ),
        "tests/test_integration.rs": (
            "//! Integration tests for {project_name}.\n"
            "\n"
            "// Re-export from the library crate if needed.\n"
            "// For now, a basic smoke test.\n"
            "\n"
            "#[test]\n"
            "fn test_smoke() {\n"
            "    assert!(true, \"Smoke test passed\");\n"
            "}\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A simple Rust project.\n"
            "\n"
            "## Prerequisites\n"
            "\n"
            "- [Rust toolchain](https://rustup.rs/)\n"
            "\n"
            "## Build\n"
            "\n"
            "```bash\n"
            "cargo build\n"
            "```\n"
            "\n"
            "## Run\n"
            "\n"
            "```bash\n"
            "cargo run\n"
            "```\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "cargo test\n"
            "```\n"
        ),
        ".gitignore": (
            "/target/\n"
            "Cargo.lock\n"
            "*.pdb\n"
            ".DS_Store\n"
            "Thumbs.db\n"
            ".idea/\n"
            ".vscode/\n"
            "*.swp\n"
        ),
    },
    "post_create_commands": [],
    "test_command": "cargo test",
}
