import os
from pathlib import Path
os.environ['SWAY_LIVE_PROOF_OUTPUT'] = '/workspace/scratch/62d002c09fd9/lut-physical-live/live-placement-proof.json'
exec(compile(Path('/workspace/scratch/62d002c09fd9/verify_live_placement.py').read_text(), '/workspace/scratch/62d002c09fd9/verify_live_placement.py', 'exec'))
