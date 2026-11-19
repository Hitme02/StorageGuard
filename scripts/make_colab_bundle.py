"""Build the files to upload to Google Drive: dist/storageguard_colab.zip and the two notebooks."""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["pyproject.toml", "README.md", "DECISIONS.md", "COLAB.md", "Makefile", "configs", "src", "tests",
           "data/bench", "colab", "scripts"]  # fmt: skip
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".hypothesis"}  # colab/templates/ ships (C15)
SKIP_NAMES = {"models_colab.yaml", ".DS_Store"}


def main() -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_colab_notebook.py")], check=True)
    if not (ROOT / "data" / "bench" / "tasks.json").exists():
        raise SystemExit("data/bench/tasks.json missing: run `sg generate-bench` first")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / "storageguard_colab.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            src = ROOT / item
            files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
            for p in files:
                rel = p.relative_to(ROOT)
                if SKIP_PARTS & set(rel.parts) or p.name in SKIP_NAMES or p.suffix == ".pyc":
                    continue
                z.write(p, Path("storageguard") / rel)
                n += 1
    notebooks = ("StorageGuard_Colab.ipynb", "StorageGuard_Colab_Continue.ipynb", "StorageGuard_Colab_Scale.ipynb",
                 "StorageGuard_Colab_Llama.ipynb", "StorageGuard_Colab_Posthoc.ipynb")  # fmt: skip
    for nb in notebooks:
        shutil.copy2(ROOT / "colab" / nb, dist / nb)
    print(f"wrote {out} ({n} files, {out.stat().st_size / 1e6:.2f} MB) and " + ", ".join(str(dist / nb) for nb in notebooks))


if __name__ == "__main__":
    main()
