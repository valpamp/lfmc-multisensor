"""Portable editable output prefixes; explicit labels cannot contain paths."""
from pathlib import Path
import re
from urllib.parse import unquote,urlsplit

RESERVED={'CON','PRN','AUX','NUL',*[f'{kind}{n}' for kind in ('COM','LPT') for n in range(1,10)]}


def validate_label(value):
    if not isinstance(value,str):raise ValueError('Filename label must be text')
    label=value.strip()
    if not label or label in ('.','..') or label.endswith('.') or re.search(r'[<>:"/\\|?*\x00-\x1f]',label):
        raise ValueError('Choose a non-empty filename label without path separators or reserved filename characters')
    if label.split('.')[0].upper() in RESERVED:raise ValueError('Filename label is a reserved Windows filename')
    return label


def default_label(aoi_path,output):
    label=Path(aoi_path).stem if aoi_path else Path(output).stem
    label=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',label).strip().rstrip('.') or 'lfmc'
    if label.split('.')[0].upper() in RESERVED:label='aoi_'+label
    return validate_label(label)


def output_label(config):
    return validate_label(config.output_label) if config.output_label is not None else default_label(config.aoi_path,config.output)


def labelled_output(config):
    return config.output.with_name(output_label(config)+config.output.suffix)


def source_product_identity(product, item):
    """Keep the authoritative catalogue name, including its native version tokens."""
    catalog_id = item.get('Id') or item['id']
    name = item.get('Name') or catalog_id
    identity = dict(catalogue_id=catalog_id)
    if product == 'landsat_c2l2':
        red = item.get('assets', {}).get('red', {}).get('href', '')
        # The Planetary Computer item ID omits the processing date. The asset
        # retains the full USGS Level-2 ID, including that date.
        asset_stem = Path(unquote(urlsplit(red).path)).stem
        full_id = re.fullmatch(r'(LC0[89]_L2(?:SP|SR)_\d{6}_\d{8})_(\d{8})_(02_T1)_SR_B4', asset_stem, re.IGNORECASE)
        if full_id and f'{full_id[1]}_{full_id[3]}'.upper() == catalog_id.upper():
            name = asset_stem[:-len('_SR_B4')]
            identity['processing_date'] = full_id[2]
        collection = re.search(r'_(\d{2})_T1$', name)
        if collection:
            identity['collection_version'] = collection[1]
    elif product == 's2_l2a':
        baseline = re.search(r'_(N\d{4})_', name)
        if baseline:
            identity['processing_baseline'] = baseline[1]
    elif product == 's3syn300':
        baseline = re.search(r'_NT_(\d{3})(?:\.SEN3)?$', name)
        if baseline:
            identity['baseline_collection'] = baseline[1]
    elif item.get('collection'):
        identity['collection_version'] = item['collection'].rsplit('.', 1)[-1]
    return dict(product_name=name, **identity)


def scene_output(config, source):
    from .models import publication_manifest

    name = source['product_name']
    suffix = Path(name).suffix
    if suffix.lower() in ('.safe', '.sen3', '.hdf', '.h5'):
        name = name.removesuffix(suffix)
    parts = [output_label(config), validate_label(name)]
    if config.product in ('modis', 'modis_aqua', 'viirs'):
        parts.append('A' + config.day.replace('-', ''))
    parts.extend(('LFMC', publication_manifest()['model_set']['map_version']))
    return config.output.with_name('_'.join(parts) + config.output.suffix)


def source_output_tags(source):
    return {'LFMC_SOURCE_' + field.upper(): value for field, value in source.items()}
