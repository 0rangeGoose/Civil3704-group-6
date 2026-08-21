"""Homography from a local metric plane to projector pixel space.

A pure homography assumes a flat plane; per the brief, that's an accepted
limitation for v1 -- vehicles at ground level are correct, tall building
masses on the physical model will cast the projection slightly off at their
tops. Computed via the standard Direct Linear Transform: build the 2N x 9
constraint matrix from N >= 4 correspondences and take the right singular
vector for the smallest singular value. With exactly 4 points this is an
exact solve; with more, it's the least-squares fit the brief asks for.
"""
from __future__ import annotations

import numpy as np


class HomographyError(ValueError):
    pass


def compute_homography(correspondences: list[tuple[float, float, float, float]]) -> np.ndarray:
    """correspondences: [(metric_x, metric_y, pixel_x, pixel_y), ...], >= 4 points.

    Returns a 3x3 matrix H such that [px, py, 1]^T ~ H @ [x, y, 1]^T (up to scale).
    """
    if len(correspondences) < 4:
        raise HomographyError(f"need at least 4 correspondences, got {len(correspondences)}")

    rows = []
    for x, y, u, v in correspondences:
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    a = np.array(rows, dtype=np.float64)

    _, _, vt = np.linalg.svd(a)
    h = vt[-1]
    matrix = h.reshape(3, 3)
    if abs(matrix[2, 2]) > 1e-12:
        matrix = matrix / matrix[2, 2]
    return matrix


def apply_homography(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    vec = matrix @ np.array([x, y, 1.0])
    if abs(vec[2]) < 1e-9:
        raise HomographyError("degenerate homography: w ~ 0 for this point")
    return vec[0] / vec[2], vec[1] / vec[2]
