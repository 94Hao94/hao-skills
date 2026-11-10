"""Small real skill packages used by installation and hook behavior tests."""

from pathlib import Path
import shutil
from scripts.skill_evolution import initialize_manifest

SOURCE = Path(__file__).resolve().parents[1]


def make_skill(path):
    """Copy executable skill parts into an isolated package and seal them."""
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    for folder in ("scripts", "references", "skills"):
        shutil.copytree(
            SOURCE / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__")
        )
    shutil.copy2(SOURCE / "SKILL.md", root / "SKILL.md")
    initialize_manifest(root)
    return root
