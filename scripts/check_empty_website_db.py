import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
value=json.loads((root/'build/website/database-before.json').read_text())
assert value[0]['results'][0]['existing_tables']==0, 'Database already initialized; do not repeat the import. Inspect prior receipt and recover only missing steps.'
print('Database empty; one initial import allowed.')
