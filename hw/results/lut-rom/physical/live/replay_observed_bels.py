import json
from pathlib import Path
import hashlib
path = Path('/workspace/scratch/62d002c09fd9/lut-physical-recovered4/observed_bels.json')
if hashlib.sha256(path.read_bytes()).hexdigest() != '55029bec8e457695188d7c7c2965f50fce40cd86999dbfa8c2dbc71e71f7dc99':
    raise RuntimeError('BEL inventory changed')
observed = json.loads(path.read_text())
if {entry.first for entry in ctx.cells} != set(observed):
    raise RuntimeError('Packed cell set differs')
for name, entry in observed.items():
    cell = ctx.cells[name]
    if cell.type != entry['type']:
        raise RuntimeError('Cell type differs: ' + name)
    if 'BEL' in cell.attrs and cell.attrs['BEL'] != entry['bel']:
        raise RuntimeError('Existing BEL differs: ' + name)
    if cell.bel:
        if cell.bel != entry['bel']:
            raise RuntimeError('Prebound BEL differs: ' + name)
    else:
        cell.setAttr('BEL', entry['bel'])
print('SWAY_OBSERVED_BELS_REPLAYED ' + str(len(observed)), flush=True)
