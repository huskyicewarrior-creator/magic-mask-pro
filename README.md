# Magic Mask Pro Studio

Magic Mask Pro Studio is a Windows-11-friendly video masking editor with a DaVinci-inspired workspace:

- Media bin + timeline track with draggable clip order.
- Trim in / trim out controls.
- Interactive mask setup before tracking (add/remove points).
- Live mask preview.
- Export to green screen, blue screen, or a custom background.
- One-click SAM2 + dependency installation flow.

## One-click install (Windows 11)

1. Open **PowerShell as Administrator**.
2. In repo root, run:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\installer\install_windows.ps1 -Launch
   ```
3. App starts at `http://localhost:8000`.

This installer will automatically:
- install Python 3.11 (if missing),
- install FFmpeg (if missing),
- create `.venv`,
- install Python dependencies,
- install SAM2 runtime dependencies.

## Manual setup (any OS)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python installer/install_sam2.py
uvicorn app.main:app --reload
```

## Editing workflow

1. Create a project.
2. Upload a video.
3. Add clips to timeline.
4. Click video to add mask points.
5. Shift + click to remove regions.
6. Tune dilation/feather for edge behavior.
7. Choose export background (green/blue/custom).
8. Export composite and download from the library panel.

## SAM2 notes

- The app can run fallback segmentation when SAM2 checkpoint is missing.
- Place your checkpoint at `sam2_checkpoint.pt` in the repository root for full SAM2 masking.
