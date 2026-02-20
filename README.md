# Magic Mask Pro

Magic Mask Pro is a reactive video masking app that uses **SAM 2 (open-source)** when available, with a robust fallback pipeline so it still runs out of the box.

## Features
- Upload videos in many formats (anything FFmpeg can decode).
- Trim start/end before processing.
- Preview overlay of the detected mask on a sampled frame.
- Background options:
  - Keep transparent-like mask composited over **green screen** or **blue screen**.
  - Upload your own background image.
- Animated, reactive UI with progress bar updates while processing.
- Exports high-quality MP4 output.
- Library tab listing generated videos with instant playback + download links.
- Includes a generated "Magic Mask Pro" SVG logo.

## Tech stack
- Python + Gradio UI
- OpenCV + FFmpeg helpers
- SAM 2 integration hook (if installed/configured)
- Fallback segmentation via rembg for broad compatibility

## Run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:7860`.

## Notes about SAM 2
The app is wired to use SAM 2 when the package/checkpoints are available. In environments without GPU/checkpoints, the app automatically falls back to rembg segmentation so the app remains functional.

To force fallback behavior:
```bash
export MAGIC_MASK_FORCE_FALLBACK=1
```

## Output library
Processed videos are saved in `outputs/` with timestamped names and surfaced in the Library tab.
