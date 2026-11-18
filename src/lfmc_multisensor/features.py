"""Publication feature equations; inputs must already have correct spatial support.

Reflectances must be in the Sentinel-2 reference domain before calling indices.
Non-reference product calibration is not performed by these functions.
"""
import numpy as np

EPS = 1e-9


def indices(red, nir, swir1):
    red, nir, swir1 = np.broadcast_arrays(
        np.asarray(red, dtype=float), np.asarray(nir, dtype=float),
        np.asarray(swir1, dtype=float),
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "NDVI": (nir - red) / (nir + red + EPS),
            "NDII": (nir - swir1) / (nir + swir1 + EPS),
            "MSI": swir1 / (nir + EPS),
            "GVMI": ((nir + .1) - (swir1 + .02)) / ((nir + .1) + (swir1 + .02) + EPS),
            "EVI2": 2.5 * (nir - red) / (nir + 2.4 * red + 1 + EPS),
        }


def vpd_from_tmean(tmean_k, vp_hpa):
    """Daily VPD in hPa from mean temperature (K) and vapour pressure (hPa)."""
    t_c = np.asarray(tmean_k, dtype=float) - 273.15
    return 6.1078 * np.exp(17.269 * t_c / (237.3 + t_c)) - np.asarray(vp_hpa, dtype=float)


def calendar_features(day):
    """Sine/cosine of day-of-year for a datetime.date-compatible value."""
    phase = 2 * np.pi * day.timetuple().tm_yday / 365.25
    return {"sin_DOY": np.sin(phase), "cos_DOY": np.cos(phase)}
