"""Template: Data Analysis Project."""

TEMPLATE: dict = {
    "name": "data_analysis",
    "display_name": "Data Analysis",
    "description": "Data analysis project with pandas and matplotlib",
    "keywords": [
        "data", "analysis", "pandas", "аналитика", "данные",
        "анализ", "аналіз", "дані", "visualization", "визуализация",
        "matplotlib", "jupyter", "notebook",
    ],
    "files": {
        "analysis.py": (
            '"""{project_name} — data analysis pipeline."""\n'
            "\n"
            "import pandas as pd\n"
            "\n"
            "\n"
            "def load_data(path: str) -> pd.DataFrame:\n"
            '    """Load data from CSV file."""\n'
            '    return pd.read_csv(path)\n'
            "\n"
            "\n"
            "def clean_data(df: pd.DataFrame) -> pd.DataFrame:\n"
            '    """Clean data: drop duplicates and NaN rows."""\n'
            "    df = df.drop_duplicates()\n"
            "    df = df.dropna()\n"
            "    return df.reset_index(drop=True)\n"
            "\n"
            "\n"
            "def describe_data(df: pd.DataFrame) -> dict:\n"
            '    """Return basic statistics about the dataframe."""\n'
            "    return {\n"
            '        "shape": df.shape,\n'
            '        "columns": list(df.columns),\n'
            '        "dtypes": df.dtypes.astype(str).to_dict(),\n'
            '        "missing": df.isnull().sum().to_dict(),\n'
            '        "describe": df.describe().to_dict(),\n'
            "    }\n"
            "\n"
            "\n"
            "def filter_data(\n"
            "    df: pd.DataFrame, column: str, value: object\n"
            ") -> pd.DataFrame:\n"
            '    """Filter rows where column equals value."""\n'
            "    return df[df[column] == value].reset_index(drop=True)\n"
            "\n"
            "\n"
            "def aggregate(\n"
            "    df: pd.DataFrame, group_by: str, agg_col: str,\n"
            '    func: str = "mean"\n'
            ") -> pd.DataFrame:\n"
            '    """Group by a column and aggregate."""\n'
            "    return (\n"
            "        df.groupby(group_by)[agg_col]\n"
            "        .agg(func)\n"
            "        .reset_index()\n"
            "    )\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    import sys\n"
            "\n"
            "    if len(sys.argv) < 2:\n"
            '        print("Usage: python analysis.py <data.csv>")\n'
            "        sys.exit(1)\n"
            "\n"
            "    df = load_data(sys.argv[1])\n"
            "    df = clean_data(df)\n"
            "    stats = describe_data(df)\n"
            '    print(f"Shape: {stats[\'shape\']}")\n'
            '    print(f"Columns: {stats[\'columns\']}")\n'
        ),
        "visualize.py": (
            '"""Visualization functions for {project_name}."""\n'
            "\n"
            "from pathlib import Path\n"
            "\n"
            "import matplotlib.pyplot as plt\n"
            "import pandas as pd\n"
            "\n"
            "\n"
            'OUTPUT_DIR = Path("output")\n'
            "\n"
            "\n"
            "def setup_style():\n"
            '    """Configure matplotlib style."""\n'
            '    plt.style.use("seaborn-v0_8-whitegrid")\n'
            "    plt.rcParams[\"figure.figsize\"] = (10, 6)\n"
            "    plt.rcParams[\"figure.dpi\"] = 100\n"
            "\n"
            "\n"
            "def plot_bar(\n"
            "    df: pd.DataFrame,\n"
            "    x: str,\n"
            "    y: str,\n"
            '    title: str = "",\n'
            '    filename: str = "bar_chart.png",\n'
            ") -> Path:\n"
            '    """Create a bar chart and save to file."""\n'
            "    setup_style()\n"
            "    fig, ax = plt.subplots()\n"
            "    ax.bar(df[x], df[y])\n"
            "    ax.set_xlabel(x)\n"
            "    ax.set_ylabel(y)\n"
            '    ax.set_title(title or f"{{y}} by {{x}}")\n'
            "    plt.tight_layout()\n"
            "\n"
            "    OUTPUT_DIR.mkdir(exist_ok=True)\n"
            "    path = OUTPUT_DIR / filename\n"
            "    fig.savefig(path)\n"
            "    plt.close(fig)\n"
            "    return path\n"
            "\n"
            "\n"
            "def plot_histogram(\n"
            "    series: pd.Series,\n"
            "    bins: int = 20,\n"
            '    title: str = "",\n'
            '    filename: str = "histogram.png",\n'
            ") -> Path:\n"
            '    """Create a histogram and save to file."""\n'
            "    setup_style()\n"
            "    fig, ax = plt.subplots()\n"
            "    ax.hist(series.dropna(), bins=bins, edgecolor=\"black\")\n"
            "    ax.set_xlabel(series.name or \"Value\")\n"
            '    ax.set_ylabel("Frequency")\n'
            '    ax.set_title(title or f"Distribution of {{series.name}}")\n'
            "    plt.tight_layout()\n"
            "\n"
            "    OUTPUT_DIR.mkdir(exist_ok=True)\n"
            "    path = OUTPUT_DIR / filename\n"
            "    fig.savefig(path)\n"
            "    plt.close(fig)\n"
            "    return path\n"
        ),
        "data/.gitkeep": "",
        "output/.gitkeep": "",
        "notebooks/.gitkeep": "",
        "tests/__init__.py": "",
        "tests/test_analysis.py": (
            '"""Tests for {project_name} analysis."""\n'
            "\n"
            "import pandas as pd\n"
            "import pytest\n"
            "\n"
            "from analysis import (\n"
            "    clean_data, describe_data, filter_data, aggregate,\n"
            ")\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def sample_df():\n"
            '    """Sample DataFrame for testing."""\n'
            "    return pd.DataFrame({\n"
            '        "name": ["Alice", "Bob", "Alice", "Charlie", "Bob"],\n'
            '        "score": [90, 85, 90, 70, 85],\n'
            '        "grade": ["A", "B", "A", "C", "B"],\n'
            "    })\n"
            "\n"
            "\n"
            "def test_clean_data_drops_duplicates(sample_df):\n"
            "    cleaned = clean_data(sample_df)\n"
            "    assert len(cleaned) == 3  # 5 rows -> 3 unique\n"
            "\n"
            "\n"
            "def test_clean_data_drops_na():\n"
            "    df = pd.DataFrame({\n"
            '        "a": [1, None, 3],\n'
            '        "b": [4, 5, None],\n'
            "    })\n"
            "    cleaned = clean_data(df)\n"
            "    assert len(cleaned) == 1\n"
            "\n"
            "\n"
            "def test_describe_data(sample_df):\n"
            "    stats = describe_data(sample_df)\n"
            '    assert stats["shape"] == (5, 3)\n'
            '    assert "name" in stats["columns"]\n'
            "\n"
            "\n"
            "def test_filter_data(sample_df):\n"
            '    filtered = filter_data(sample_df, "name", "Alice")\n'
            "    assert len(filtered) == 2\n"
            '    assert all(filtered["name"] == "Alice")\n'
            "\n"
            "\n"
            "def test_aggregate(sample_df):\n"
            '    result = aggregate(sample_df, "name", "score", "mean")\n'
            "    assert len(result) == 3\n"
            '    assert "score" in result.columns\n'
        ),
        "requirements.txt": (
            "pandas>=2.2\n"
            "matplotlib>=3.9\n"
            "numpy>=1.26\n"
            "openpyxl>=3.1\n"
            "jupyter>=1.1\n"
            "pytest>=8.0\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A data analysis project using pandas and matplotlib.\n"
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
            "# Analyze a CSV file\n"
            "python analysis.py data/your_data.csv\n"
            "```\n"
            "\n"
            "## Project Structure\n"
            "\n"
            "```\n"
            "data/          — raw data files\n"
            "output/        — charts and processed data\n"
            "notebooks/     — Jupyter notebooks\n"
            "analysis.py    — data loading and transformation\n"
            "visualize.py   — plotting functions\n"
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
            "output/*.png\n"
            "output/*.csv\n"
            ".ipynb_checkpoints/\n"
            "data/*.csv\n"
            "!data/.gitkeep\n"
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "Data analysis project"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "pandas>=2.2",\n'
            '    "matplotlib>=3.9",\n'
            '    "numpy>=1.26",\n'
            '    "openpyxl>=3.1",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0", "jupyter>=1.1"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
