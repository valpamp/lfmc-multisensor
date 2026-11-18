"""Frozen reflectance calibration for the publication's central-pixel models."""
import numpy as np

BANDS = ("red", "nir", "swir1")
MODE = "srf_screened_foldwise_robust_linear"


def validate_preprocessing(preprocessing):
    """Fail closed on metadata incompatible with the audited publication method."""
    expected = {
        "mode": MODE, "reference_products": ["s2_l2a"],
        "bands": list(BANDS), "fit_stat": "mean",
        "apply_stats": ["core", "mean", "median"],
        "clip_min": -0.05, "clip_max": 1.5,
    }
    for key, value in expected.items():
        if preprocessing.get(key) != value:
            raise ValueError(f"Unsupported preprocessing setting: {key}")
    calibration = preprocessing.get("calibration")
    if not isinstance(calibration, dict) or not calibration:
        raise ValueError("Missing frozen calibration")
    for domain, bands in calibration.items():
        for band in BANDS:
            entry = bands.get(band, {})
            if entry.get("status") != "ok":
                raise ValueError(f"Unavailable calibration: {domain}/{band}")
            for coefficient in ("slope", "intercept"):
                if not np.isfinite(float(entry.get(coefficient, np.nan))):
                    raise ValueError(f"Invalid calibration: {domain}/{band}/{coefficient}")


def calibrate_reflectance(bundle, reflectance, *, product, sensor):
    """Return fresh float64 red/NIR/SWIR1 arrays in the S2 reference domain.

    Inputs are provider-scaled reflectances on the audited product support,
    with product QA already applied. Decode the training sampler's -999999
    marker before calibration/clipping. S2 is normalized but never clipped.
    Non-reference product/sensor names must match a frozen bundle domain;
    no fitting, aliases or fallback to uncalibrated reflectance is performed.
    """
    preprocessing = bundle["predictor_preprocessing"]
    validate_preprocessing(preprocessing)
    if not isinstance(product, str) or not isinstance(sensor, str):
        raise ValueError("product and sensor must be explicit strings")
    product, sensor = product.strip(), sensor.strip()
    if not product or not sensor:
        raise ValueError("product and sensor must be nonempty")
    domain = f"{product}::{sensor}"
    reference = product in preprocessing["reference_products"]
    if not reference and domain not in preprocessing["calibration"]:
        raise ValueError(f"Unsupported calibration domain: {domain}")
    result = {}
    shape = None
    for band in BANDS:
        if band not in reflectance:
            raise ValueError(f"Missing reflectance band: {band}")
        values = np.asarray(reflectance[band], dtype=float).copy()
        if shape is None:
            shape = values.shape
        if values.shape != shape:
            raise ValueError(f"Reflectance shape mismatch: {band}")
        values[values == -999999.0] = np.nan
        if not reference:
            params = preprocessing["calibration"][domain][band]
            values = np.clip(values * float(params["slope"]) + float(params["intercept"]),
                             preprocessing["clip_min"], preprocessing["clip_max"])
        result[band] = values
    return result
