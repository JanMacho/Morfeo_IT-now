"""Test de Kolmogorov-Smirnov de dos muestras, en numpy.

Se implementa aquí para no depender de scipy. El estadístico es exacto; el
p-valor es el asintótico de Kolmogorov con la corrección de Stephens:

    en = sqrt(n m / (n + m)),  lambda = (en + 0.12 + 0.11 / en) D
    Q(lambda) = 2 suma_{k>=1} (-1)^(k-1) exp(-2 k^2 lambda^2)

Con muestras discretas (por ejemplo, número de peticiones por sesión) el test
es conservador. Con muestras grandes el p-valor detecta diferencias mínimas, por
eso la validación decide con el estadístico D y un umbral, y el p-valor se da
como información.
"""

from __future__ import annotations

import math

import numpy as np


def kolmogorov_q(lam: float) -> float:
    """Función de supervivencia de la distribución de Kolmogorov."""
    if lam < 1e-3:
        return 1.0
    total = 0.0
    for k in range(1, 101):
        term = 2.0 * (-1) ** (k - 1) * math.exp(-2.0 * k * k * lam * lam)
        total += term
        if abs(term) < 1e-12:
            break
    return min(1.0, max(0.0, total))


def ks_2samp(a: np.ndarray | list[float], b: np.ndarray | list[float]) -> tuple[float | None, float | None]:
    """Estadístico D y p-valor asintótico. ``(None, None)`` si alguna muestra está vacía."""
    x = np.sort(np.asarray(a, dtype=float))
    y = np.sort(np.asarray(b, dtype=float))
    x = x[np.isfinite(x)]
    y = y[np.isfinite(y)]
    n, m = x.size, y.size
    if n == 0 or m == 0:
        return None, None
    points = np.concatenate([x, y])
    cdf_x = np.searchsorted(x, points, side="right") / n
    cdf_y = np.searchsorted(y, points, side="right") / m
    d = float(np.max(np.abs(cdf_x - cdf_y)))
    en = math.sqrt(n * m / (n + m))
    return d, kolmogorov_q((en + 0.12 + 0.11 / en) * d)
