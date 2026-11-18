"""Complete-feature prediction for one vegetation-class bundle.

Caller supplies calibrated/prepared feature arrays and class/QA eligibility.
This primitive neither loads models nor chooses a vegetation model.
"""
import numpy as np


def predict_features(bundle, features, eligible, *, batch_size=50000):
    """Return float32 LFMC with NaN for ineligible or incomplete pixels.

    Feature order, scaler, forest and inverse target transform come from the
    bundle. Shapes must match exactly; no implicit spatial broadcasting occurs.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    mask = np.asarray(eligible)
    if mask.dtype != np.bool_:
        raise ValueError("eligible must be a boolean array")
    mask = mask.copy()
    names = bundle["features"]
    if not len(names) or len(set(names)) != len(names):
        raise ValueError("Bundle feature names must be nonempty and unique")
    arrays = []
    for name in names:
        if name not in features:
            raise ValueError(f"Missing feature: {name}")
        array = np.asarray(features[name], dtype=float)
        if array.shape != mask.shape:
            raise ValueError(f"Feature shape mismatch: {name}")
        mask &= np.isfinite(array)
        arrays.append(array.ravel())
    output = np.full(mask.shape, np.nan, dtype=np.float32)
    selected = np.flatnonzero(mask.ravel())
    for start in range(0, len(selected), batch_size):
        ids = selected[start:start + batch_size]
        x = np.column_stack([array[ids] for array in arrays])
        raw = bundle["model"].predict(bundle["scaler"].transform(x))
        y = np.asarray(bundle["qt_target"].inverse_transform(np.asarray(raw).reshape(-1, 1))).ravel()
        if len(y) != len(ids) or not np.isfinite(y).all():
            raise ValueError("Model produced invalid predictions")
        output.ravel()[ids] = y.astype(np.float32)
    return output
