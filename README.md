# Magic Mask Pro

Magic Mask Pro is a reactive video masking app that uses **SAM 2** (when available) to isolate objects in video and replace the background.

## Why masking may flicker

If `sam2_checkpoint.pt` is missing, the app switches to an OpenCV fallback model, which is less stable and can flicker.
You can verify active engine in the UI (`Segmentation engine: ...`) and via `/api/health`.

## Features

- Click-to-mask with **positive and negative points** (you choose what is kept/removed).
- Preview overlay before rendering.
- No-trim mode + trim segment preview.
- Background selection/upload.
- Progress bar while rendering.
- Library with **download and delete**.

## Run

1. `cd /workspace/magic-mask-pro`
2. `python -m venv .venv`
3. `source .venv/bin/activate`
4. `pip install -r requirements.txt`
5. `uvicorn app.main:app --reload`
6. Open `http://localhost:8000`

## Enable true SAM2

1. Install SAM2 Python package in your environment.
2. Place model checkpoint as `sam2_checkpoint.pt` in repo root.
3. Restart app.

When enabled, `/api/health` should return `"segmentation_engine": "sam2"`.
