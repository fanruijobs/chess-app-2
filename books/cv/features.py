"""Glyph feature vectors: Hu moments + pixel stats, z-scored per book."""

import cv2
import numpy as np

FEATURE_DIM = 10
DOWNSAMPLE = 24


def glyph_features(glyph, downsample: int = DOWNSAMPLE) -> np.ndarray:
    """10-dim feature vector for one glyph (7 Hu moments + 3 pixel stats)."""
    mask = cv2.resize(glyph.mask.astype(np.uint8), (downsample, downsample))
    patch = cv2.resize(glyph.patch, (downsample, downsample)).astype(np.float32) / 255.0
    moments = cv2.moments(mask)
    hu = cv2.HuMoments(moments).ravel()
    hu = -np.sign(hu) * np.log10(np.abs(hu) + 1e-10)
    stats = np.array([patch.mean(), patch.std(), float(glyph.fg_ratio)])
    return np.concatenate([hu, stats])


def standardize(vector: np.ndarray, means: np.ndarray, stds: np.ndarray) -> np.ndarray:
    return (vector - means) / np.maximum(stds, 1e-6)
