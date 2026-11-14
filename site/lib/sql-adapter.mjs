// Physical execution only. Search parsing, ranking, and cursor identity stay in the accepted core.
export const EXECUTION = 'workbench-20261114-1';
const membership = /EXISTS \(SELECT 1 FROM json_each\((v\.\w+|json_extract\(v\.values_json,'\$\.\w+'\))\) term JOIN json_each\((\?\d+)\) wanted ON term\.value=wanted\.value\)/g;

export function optimizeSQL(sql, unclassified = false) {
  const rewrite = text => text.replace(
    'FROM file_variants v JOIN file_variants_fts f ON f.variant_id=v.variant_id',
    'FROM file_variants_fts f CROSS JOIN file_variants v ON f.variant_id=v.variant_id'
  ).replace(membership, (_match, column, param) => {
    if (unclassified && column === "json_extract(v.values_json,'$.category')" &&
        sql.params[Number(param.slice(1))-1] === '["mga_unclassified"]') {
      return `(${param} IS NOT NULL AND v.family_id NOT IN (
        SELECT DISTINCT family_id FROM file_variants
        WHERE eligible=1 AND COALESCE(json_array_length(values_json,'$.category'),0)>0
      ))`;
    }
    return `EXISTS (SELECT 1 FROM json_each(${column}) term WHERE term.value IN (SELECT value FROM json_each(${param})))`;
  });
  const start = ' FROM file_variants v';
  const from = sql.counts.indexOf(start);
  const end = sql.counts.indexOf('), representatives AS', from);
  if (from < 0 || end < 0) throw new Error('Unsupported accepted SQL shape');
  const predicate = rewrite(sql.counts.slice(from, end));
  // Numbered parameters are shared by the core queries; retain the final binding
  // even when counting does not need scores or project pins.
  const counts = `SELECT count(*) AS qualified_variants,count(DISTINCT v.family_id) AS qualified_families${predicate} AND ?${sql.params.length} IS NOT NULL`;
  return {...sql, counts, window:rewrite(sql.window), pins:rewrite(sql.pins)};
}
