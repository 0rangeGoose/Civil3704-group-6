import numpy as np
import pytest

from engine.publish.homography import HomographyError, apply_homography, compute_homography


def test_recovers_a_simple_affine_transform():
    # pixel = (2x + 100, 3y + 50) -- a homography with no perspective terms.
    metric_points = [(0, 0), (10, 0), (0, 10), (10, 10)]
    correspondences = [(x, y, 2 * x + 100, 3 * y + 50) for x, y in metric_points]

    matrix = compute_homography(correspondences)

    for x, y in metric_points + [(5, 5), (-3, 7)]:
        px, py = apply_homography(matrix, x, y)
        assert px == pytest.approx(2 * x + 100, abs=1e-6)
        assert py == pytest.approx(3 * y + 50, abs=1e-6)


def test_recovers_a_general_projective_transform():
    # An arbitrary, non-degenerate homography with real perspective terms.
    true_h = np.array([
        [1.2, 0.3, 400.0],
        [-0.1, 1.5, 150.0],
        [0.0007, 0.0003, 1.0],
    ])
    metric_points = [(0, 0), (100, 0), (0, 100), (100, 100), (50, 20)]
    correspondences = []
    for x, y in metric_points:
        px, py = apply_homography(true_h, x, y)
        correspondences.append((x, y, px, py))

    recovered = compute_homography(correspondences)

    for x, y in metric_points + [(30, 70)]:
        expected_px, expected_py = apply_homography(true_h, x, y)
        px, py = apply_homography(recovered, x, y)
        assert px == pytest.approx(expected_px, abs=1e-3)
        assert py == pytest.approx(expected_py, abs=1e-3)


def test_least_squares_fit_improves_with_more_noisy_points():
    true_h = np.array([[2.0, 0.0, 500.0], [0.0, 2.0, 300.0], [0.0, 0.0, 1.0]])
    rng = np.random.default_rng(42)
    metric_points = [(x, y) for x in range(0, 100, 20) for y in range(0, 100, 20)]  # 25 points

    correspondences = []
    for x, y in metric_points:
        px, py = apply_homography(true_h, x, y)
        px += rng.normal(0, 0.5)
        py += rng.normal(0, 0.5)
        correspondences.append((x, y, px, py))

    recovered = compute_homography(correspondences)

    # Check against a held-out noise-free point.
    px, py = apply_homography(recovered, 40, 40)
    expected_px, expected_py = apply_homography(true_h, 40, 40)
    assert px == pytest.approx(expected_px, abs=2.0)
    assert py == pytest.approx(expected_py, abs=2.0)


def test_requires_at_least_four_points():
    correspondences = [(0, 0, 0, 0), (1, 0, 1, 0), (0, 1, 0, 1)]
    with pytest.raises(HomographyError):
        compute_homography(correspondences)
