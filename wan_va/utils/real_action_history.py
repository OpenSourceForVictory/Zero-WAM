"""Validate complete real-robot chunks without importing model dependencies."""

import numpy as np


def validate_history(actions, observations, shape, first):
    array = np.asarray(actions)
    frames = shape[1] - int(first)
    if array.shape != tuple(shape) or not np.isfinite(array).all():
        raise ValueError("Invalid executed action history shape or values")
    if not isinstance(observations, list) or len(observations) != frames * 4:
        raise ValueError(
            "Only complete, aligned observation/action chunks may be cached"
        )
