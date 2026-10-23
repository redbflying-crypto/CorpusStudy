import shutil
from pathlib import Path

from corpusstudy.config import get_settings


def copy_bundle_skeleton(dest: Path) -> Path:
    """A copy of the committed bundle with only its structure and source references:
    index/log files and references/, but none of the drafted knowledge concepts."""

    def ignore(directory: str, names: list[str]) -> list[str]:
        rel = Path(directory).relative_to(get_settings().knowledge_dir).as_posix()
        if rel.startswith("references"):
            return []
        return [n for n in names if n.endswith(".md") and n not in ("index.md", "log.md")]

    shutil.copytree(get_settings().knowledge_dir, dest, ignore=ignore)
    return dest
