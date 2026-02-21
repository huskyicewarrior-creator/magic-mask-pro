from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    run([sys.executable, "-m", "pip", "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu121"])
    run([sys.executable, "-m", "pip", "install", "git+https://github.com/facebookresearch/sam2.git"])
    ckpt = ROOT / "sam2_checkpoint.pt"
    if not ckpt.exists():
      print("SAM2 checkpoint not found at sam2_checkpoint.pt. Add checkpoint to enable true SAM2 inference.")
    print("SAM2 dependencies installed.")
