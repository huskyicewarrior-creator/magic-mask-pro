from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class SegmentationConfig:
    sam2_checkpoint: Optional[Path] = None
    sam2_model_cfg: str = "sam2_hiera_l.yaml"


class Sam2Segmenter:
    """Attempts SAM2 first, then falls back to OpenCV GrabCut.

    Supports multi-point prompts with positive (1) and negative (0) labels.
    """

    def __init__(self, config: SegmentationConfig):
        self.config = config
        self._sam_predictor = None
        self._prev_gray: Optional[np.ndarray] = None
        self._prev_mask: Optional[np.ndarray] = None
        self._init_sam2()

    @property
    def using_sam2(self) -> bool:
        return self._sam_predictor is not None

    def _init_sam2(self) -> None:
        if not self.config.sam2_checkpoint or not self.config.sam2_checkpoint.exists():
            return
        try:
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor

            model = build_sam2(self.config.sam2_model_cfg, str(self.config.sam2_checkpoint), device="cpu")
            self._sam_predictor = SAM2ImagePredictor(model)
        except Exception:
            self._sam_predictor = None

    def _mask_from_sam2(self, frame: np.ndarray, points: Sequence[Tuple[int, int]], labels: Sequence[int]) -> Optional[np.ndarray]:
        if self._sam_predictor is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._sam_predictor.set_image(rgb)
        coords = np.array([[p[0], p[1]] for p in points], dtype=np.float32)
        lbls = np.array(labels, dtype=np.int32)
        masks, scores, _ = self._sam_predictor.predict(
            point_coords=coords,
            point_labels=lbls,
            multimask_output=True,
        )
        best = masks[np.argmax(scores)]
        return (best.astype(np.uint8)) * 255

    def _mask_from_grabcut(self, frame: np.ndarray, points: Sequence[Tuple[int, int]], labels: Sequence[int]) -> np.ndarray:
        h, w = frame.shape[:2]
        gc_mask = np.full((h, w), cv2.GC_PR_BGD, dtype=np.uint8)

        fg_points = [p for p, l in zip(points, labels) if l == 1]
        bg_points = [p for p, l in zip(points, labels) if l == 0]

        if fg_points:
            xs = [p[0] for p in fg_points]
            ys = [p[1] for p in fg_points]
            pad_x, pad_y = max(30, w // 10), max(30, h // 10)
            x1 = max(0, min(xs) - pad_x)
            y1 = max(0, min(ys) - pad_y)
            x2 = min(w - 1, max(xs) + pad_x)
            y2 = min(h - 1, max(ys) + pad_y)
            gc_mask[y1:y2, x1:x2] = cv2.GC_PR_FGD

        for x, y in fg_points:
            cv2.circle(gc_mask, (int(x), int(y)), 14, cv2.GC_FGD, -1)
        for x, y in bg_points:
            cv2.circle(gc_mask, (int(x), int(y)), 14, cv2.GC_BGD, -1)

        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)
        cv2.grabCut(frame, gc_mask, None, bgd_model, fgd_model, 4, cv2.GC_INIT_WITH_MASK)
        out = np.where((gc_mask == cv2.GC_BGD) | (gc_mask == cv2.GC_PR_BGD), 0, 255).astype(np.uint8)
        return out

    def reset(self) -> None:
        self._prev_gray = None
        self._prev_mask = None

    def _stabilize_mask(self, frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._prev_gray is not None and self._prev_mask is not None:
            flow = cv2.calcOpticalFlowFarneback(self._prev_gray, gray, None, 0.5, 2, 15, 2, 5, 1.1, 0)
            h, w = gray.shape
            grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
            map_x = (grid_x + flow[..., 0]).astype(np.float32)
            map_y = (grid_y + flow[..., 1]).astype(np.float32)
            warped_prev = cv2.remap(self._prev_mask, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            mixed = cv2.addWeighted(mask.astype(np.float32), 0.7, warped_prev.astype(np.float32), 0.3, 0)
            mask = (mixed > 127).astype(np.uint8) * 255

        self._prev_gray = gray
        self._prev_mask = mask
        return mask

    def get_mask(self, frame: np.ndarray, points: List[Tuple[int, int]], labels: List[int]) -> np.ndarray:
        if not points or not labels or len(points) != len(labels):
            raise ValueError("points/labels are required and must have same length")

        sam_mask = self._mask_from_sam2(frame, points, labels)
        raw_mask = sam_mask if sam_mask is not None else self._mask_from_grabcut(frame, points, labels)
        mask = cv2.medianBlur(raw_mask, 5)
        return self._stabilize_mask(frame, mask)
