# Magic Mask Pro

Magic Mask Pro is a reactive video masking app that uses **SAM 2** (when available) to isolate an object in video and replace the background.

## What works now

- Object pick + preview mask overlay.
- **No Trim** toggle (full video) and manual trim start/end.
- **Trim segment preview** in the player before rendering.
- Background selection (green screen, blue screen, or uploaded image).
- Async processing with live progress bar.
- Library view with playback and MP4 download.
- High-quality MP4 export.
- Animated responsive UI + logo.

## Step-by-step: run locally

1. Open a terminal in the project root:
   ```bash
   cd /workspace/magic-mask-pro
   ```
2. Create and activate virtualenv:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Start server:
   ```bash
   uvicorn app.main:app --reload
   ```
5. Open browser:
   ```
   http://localhost:8000
   ```

## Step-by-step: use the app

1. Upload a video.
2. Click your object in the video to set mask target.
3. Click **Generate Preview Overlay** to verify mask.
4. Choose background (green/blue/custom upload).
5. Either:
   - keep **No Trim** checked for full video, or
   - uncheck it, set Start/End, then click **Preview Trim Segment**.
6. Click **Render High Quality Video**.
7. Watch progress bar.
8. Download final MP4 from **Library**.

## SAM 2 setup (optional)

If SAM2 package + checkpoint are available, app uses SAM2 automatically:

1. Install SAM2 in your environment.
2. Place checkpoint file at `sam2_checkpoint.pt` in repo root.
3. Restart server.

If SAM2 is unavailable, app falls back to OpenCV segmentation.
