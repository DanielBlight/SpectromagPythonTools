"""Export reviewed source only; never include Git metadata, data or local configs."""

import argparse
import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project.resolve()
    files = []
    for folder in ("src", "scripts", "tests", "docs"):
        directory = root / folder
        if directory.exists():
            files.extend(
                p
                for p in directory.rglob("*")
                if p.is_file()
                and not p.is_symlink()
                and p.suffix.lower() in (".py", ".md", ".rst", ".txt", ".ps1", ".cmd")
                and "__pycache__" not in p.parts
            )
    for name in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        ".gitignore",
        ".python-version",
    ):
        if (root / name).is_file():
            files.append(root / name)
    manifest = {"created": datetime.now().isoformat(), "files": {}}
    with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            manifest["files"][relative] = hashlib.sha256(content).hexdigest()
            archive.writestr(relative, content)
        archive.writestr("TRANSFER_MANIFEST.json", json.dumps(manifest, indent=2))
    print("Created:", args.output)
    print(
        "Extract to a staging folder on development PC. Review differences before copying into the Git checkout."
    )
    print(
        "No deletions inferred. No .git, data, logs, virtual environments or local instrument config included."
    )


if __name__ == "__main__":
    main()
