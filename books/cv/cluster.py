"""Agglomerative glyph clustering, pure numpy (centroid linkage)."""

import numpy as np


def agglomerative(vectors: np.ndarray, cut: float = 0.35) -> tuple[np.ndarray, np.ndarray]:
    """Cluster rows of `vectors`; returns (labels, centroids).

    Centroid-linkage agglomerative clustering: merge the closest pair of
    cluster centroids until the minimum distance exceeds `cut` (in the
    vectors' own distance units — pass z-scored vectors for a comparable cut).
    """
    n = len(vectors)
    if n == 0:
        return np.array([], dtype=int), np.zeros((0, vectors.shape[1]))
    if n == 1:
        return np.zeros(1, dtype=int), vectors.copy()

    centroids = vectors.astype(np.float64).copy()
    members = {i: [i] for i in range(n)}
    active = set(range(n))

    while len(active) > 1:
        idx = sorted(active)
        d = np.linalg.norm(
            centroids[idx][:, None, :] - centroids[idx][None, :, :], axis=2
        )
        np.fill_diagonal(d, np.inf)
        flat = np.argmin(d)
        a, b = np.unravel_index(flat, d.shape)
        if d[a, b] > cut:
            break
        ia, ib = idx[a], idx[b]
        na, nb = len(members[ia]), len(members[ib])
        centroids[ia] = (centroids[ia] * na + centroids[ib] * nb) / (na + nb)
        members[ia].extend(members[ib])
        active.discard(ib)

    remaining = sorted(active)
    labels = np.full(n, -1, dtype=int)
    for new_id, old_id in enumerate(remaining):
        for member in members[old_id]:
            labels[member] = new_id
    return labels, centroids[remaining]
