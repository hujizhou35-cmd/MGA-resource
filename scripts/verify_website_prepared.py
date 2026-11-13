"""Verify exact preparation pins before deploying; never recompute acceptance."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
read=lambda p:json.loads(p.read_text(encoding='utf8'))
out=root/'build/website';receipt=read(out/'prepare-receipt.json')
assert receipt['run_id']=='43485204419'
assert receipt['accepted_bindings']['catalog_sha256']=='f3f869f4ed24e4582ff26c46fabb64a1e68593d2a731eff66e728ec6b280f377'
assert receipt['sql_sha256']==hashlib.sha256((out/'search.sql').read_bytes()).hexdigest()
assert receipt['formal_report_sha256']==hashlib.sha256((root/'data/acceptance-reports/component-43481252923.json').read_bytes()).hexdigest()
assert receipt['accepted_bindings']['search_core_sha256']==hashlib.sha256((root/'mga_resources/search-core.mjs').read_bytes()).hexdigest()
assert read(root/'data/diagnostics/cloudflare-free-plan-20261114.json')['observed_current_plan']=='Free'
print(json.dumps({'prepared_verified':True,'variant_rows':receipt['variant_rows'],'objects':receipt['objects']}))
