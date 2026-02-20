from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass
class SegmentationConfig:
    sam2_checkpoint: Optional[Path] = None
    sam2_model_cfg: str = "sam2_hiera_l.yaml"


class Sam2Segmenter:
    """Attempts to use SAM2 when available, falls back to GrabCut.

    The fallback keeps this application functional without model files.
    """

    def __init__(self, config: SegmentationConfig):
        self.config = config
        self._sam_predictor = None
        self._tracked_point: Optional[np.ndarray] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._init_sam2()

    def _init_sam2(self) -> None:
        if not self.config.sam2_checkpoint:
            return
        if not self.config.sam2_checkpoint.exists():
            return
        try:
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor

            model = build_sam2(self.config.sam2_model_cfg, str(self.config.sam2_checkpoint), device="cpu")
            self._sam_predictor = SAM2ImagePredictor(model)
        except Exception:
            self._sam_predictor = None

    def _mask_from_sam2(self, frame: np.ndarray, point: Tuple[int, int]) -> Optional[np.ndarray]:
        if self._sam_predictor is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._sam_predictor.set_image(rgb)
        masks, scores, _ = self._sam_predictor.predict(
            point_coords=np.array([[point[0], point[1]]]),
            point_labels=np.array([1]),
            multimask_output=True,
        )
        best = masks[np.argmax(scores)]
        return (best.astype(np.uint8)) * 255

    def _mask_from_grabcut(self, frame: np.ndarray, point: Tuple[int, int]) -> np.ndarray:
        h, w = frame.shape[:2]
        x, y = point
        box_w = max(50, w // 4)
        box_h = max(50, h // 4)
        x1 = int(max(0, min(w - 1, x - box_w // 2)))
        y1 = int(max(0, min(h - 1, y - box_h // 2)))
        x2 = int(min(w - 1, x1 + box_w))
        y2 = int(min(h - 1, y1 + box_h))

        mask = np.zeros(frame.shape[:2], np.uint8)
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)
        rect = (x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        cv2.grabCut(frame, mask, rect, bgd_model, fgd_model, 3, cv2.GC_INIT_WITH_RECT)
        output = np.where((mask == 2) | (mask == 0), 0, 255).astype("uint8")
        return output

    def _track_point(self, frame: np.ndarray, initial_point: Tuple[int, int]) -> Tuple[int, int]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._tracked_point is None:
            self._tracked_point = np.array([[initial_point]], dtype=np.float32)
            self._prev_gray = gray
            return initial_point

        if self._prev_gray is not None:
            next_point, status, _ = cv2.calcOpticalFlowPyrLK(
                self._prev_gray,
                gray,
                self._tracked_point,
                None,
                winSize=(21, 21),
                maxLevel=2,
            )
            if status is not None and status[0][0] == 1:
                self._tracked_point = next_point

        self._prev_gray = gray
        pt = self._tracked_point[0][0]
        return int(pt[0]), int(pt[1])

    def reset(self) -> None:
        self._tracked_point = None
        self._prev_gray = None

    def get_mask(self, frame: np.ndarray, initial_point: Tuple[int, int]) -> np.ndarray:
        point = self._track_point(frame, initial_point)
        sam_mask = self._mask_from_sam2(frame, point)
        if sam_mask is not None:
            return sam_mask
        return self._mask_from_grabcut(frame, point)
