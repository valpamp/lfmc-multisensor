"""Hash-verified loading of trusted published bundles; no downloads or refitting."""
import hashlib
from importlib.resources import files
from importlib.metadata import version as installed_version
import io
import json

import joblib

from .calibration import validate_preprocessing


def publication_manifest():
    return json.loads(files("lfmc_multisensor").joinpath("publication_models.json").read_text())


def model_provenance():
    """Release versions assigned to the existing immutable publication bytes."""
    manifest=publication_manifest()
    return dict(model_set=dict(manifest['model_set']),artifacts={
        vegetation:dict(version=spec['version'],sha256=spec['sha256'])
        for vegetation,spec in manifest['artifacts'].items()})


def model_output_tags():
    provenance=model_provenance()
    model_set=provenance['model_set']
    tags=dict(LFMC_MODEL_SET_ID=model_set['id'],LFMC_MODEL_SET_VERSION=model_set['version'],
              LFMC_MODEL_MAP_VERSION=model_set['map_version'],LFMC_PAPER_DOI=model_set['paper_doi'],
              LFMC_PAPER_URL='https://doi.org/'+model_set['paper_doi'])
    for vegetation,spec in provenance['artifacts'].items():
        tags['LFMC_MODEL_VERSION_'+vegetation.upper()]=spec['version']
        tags['LFMC_MODEL_SHA256_'+vegetation.upper()]=spec['sha256']
    return tags


def validate_bundle(bundle, vegetation):
    spec = publication_manifest()["artifacts"].get(vegetation)
    if spec is None:
        raise ValueError(f"Unsupported vegetation: {vegetation}")
    expected = {"veg_type": vegetation, "pixel_stat": "core", "target_mode": "LFMC_mean",
                "requires_complete_features": True, "validation_mode_selected": "gapfill_within_site"}
    for key, value in expected.items():
        if bundle.get(key) != value:
            raise ValueError(f"Invalid published bundle field: {key}")
    if list(bundle.get("features", [])) != spec["features"]:
        raise ValueError("Published feature order mismatch")
    for key in ("model", "scaler", "qt_target"):
        if type(bundle[key]).__name__ != spec[{"model": "model_class", "scaler": "scaler_class", "qt_target": "target_class"}[key]]:
            raise ValueError(f"Unexpected serialized component: {key}")
    for key in ("model", "scaler"):
        if bundle[key].n_features_in_ != len(spec["features"]):
            raise ValueError(f"Feature count mismatch: {key}")
    if bundle["qt_target"].n_features_in_ != 1:
        raise ValueError("Target transform must have one feature")
    validate_preprocessing(bundle["predictor_preprocessing"])
    if sorted(bundle["predictor_preprocessing"]["calibration"]) != spec["calibration_domains"]:
        raise ValueError("Published calibration domains mismatch")


def load_published_bundle(path, vegetation):
    """Load only bytes matching the known publication SHA256, then validate.

    The in-memory byte stream ensures the bytes checked are the bytes loaded.
    This loads Python serialization and must only be used with trusted artifacts.
    """
    from pathlib import Path
    spec = publication_manifest()["artifacts"].get(vegetation)
    if spec is None:
        raise ValueError(f"Unsupported vegetation: {vegetation}")
    required=publication_manifest()['serialization_runtime']['scikit_learn']
    actual=installed_version('scikit-learn')
    if actual!=required:
        raise ValueError(f'Published bundles require scikit-learn {required}; installed {actual}. Install the pinned workflow environment.')
    payload = Path(path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != spec["sha256"]:
        raise ValueError(f"Published artifact hash mismatch: {vegetation}")
    bundle = joblib.load(io.BytesIO(payload))
    validate_bundle(bundle, vegetation)
    return bundle
