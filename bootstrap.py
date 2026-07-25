from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"
STATE_DIR = ROOT / ".bootstrap"
HASH_FILE = STATE_DIR / "requirements.sha256"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def run(command: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> int:
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed.returncode


def dependencies_ready() -> bool:
    try:
        import playwright  # noqa: F401
        import rich  # noqa: F401
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return False
    return HASH_FILE.exists() and HASH_FILE.read_text(encoding="utf-8").strip() == file_hash(REQUIREMENTS)


def install_dependencies() -> None:
    print("[KURULUM] Python kütüphaneleri hazırlanıyor...")
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    run([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(file_hash(REQUIREMENTS), encoding="utf-8")
    print("[KURULUM] Bağımlılıklar hazır. Gemini için kurulu Google Chrome kullanılacak.")


def main() -> int:
    if not dependencies_ready():
        install_dependencies()

    env = os.environ.copy()
    src_path = str(ROOT / "src")
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    return run([sys.executable, "-m", "os_agent.main", *sys.argv[1:]], env=env, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
