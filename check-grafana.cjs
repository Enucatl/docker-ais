// Run: node check-grafana.cjs (requires the running AIS database).
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { execFileSync } = require('node:child_process');
const dashboard = JSON.parse(readFileSync(`${__dirname}/grafana-dashboard.json`, 'utf8'));

function query(sql, from, to, fixture = false) {
  sql = sql.replace(/\$__timeFilter\((\w+)\)/g, `$1 BETWEEN '${from}'::timestamptz AND '${to}'::timestamptz`)
    .replaceAll('$__timeFrom()', `'${from}'`).replaceAll('$__timeTo()', `'${to}'`);
  if (fixture) {
    const data = `fixture AS (
      SELECT received_at, source_hash, '311001056'::text AS mmsi,
             latitude, longitude, 0::double precision AS speed_over_ground_knots,
             ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) AS position
      FROM (VALUES
        ('2026-01-01T00:00:00Z'::timestamptz, 'a', 0::double precision, 0::double precision),
        ('2026-01-01T00:00:10Z'::timestamptz, 'b', 0::double precision, 0::double precision),
        ('2026-01-01T00:01:10Z'::timestamptz, 'c', 0::double precision, 0.001::double precision)
      ) AS samples(received_at, source_hash, latitude, longitude)
    )`;
    sql = sql.replaceAll('public.position_reports', 'fixture');
    sql = /^WITH\s/i.test(sql) ? sql.replace(/^WITH\s/i, `WITH ${data}, `) : `WITH ${data} ${sql}`;
  }
  const output = execFileSync('docker', ['compose', 'exec', '-T', 'ais-postgres',
    'psql', '-U', 'ais', '-d', 'ais', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1'], {
    cwd: __dirname, encoding: 'utf8',
    input: `SET ROLE ais_grafana; SET TIME ZONE 'UTC'; BEGIN READ ONLY;
      SELECT coalesce(json_agg(result), '[]'::json) FROM (${sql}) AS result; ROLLBACK;`
  });
  return JSON.parse(output);
}

const sql = dashboard.panels.map(panel => panel.targets[0].rawSql);
assert.equal(sql.length, 4);
const fixture = sql.map(statement => query(statement, '2026-01-01T00:00:00Z', '2026-01-01T02:00:00Z', true));
assert.deepEqual(fixture[0].map(row => row['Interval (seconds)']), [10, 60]);
assert.equal(fixture[1][0]['Distance (metres)'], 0);
assert.ok(Math.abs(fixture[1][1]['Distance (metres)'] - 111.31949) < 0.01);
assert.deepEqual(fixture[2].map(row => row['Stored updates']), [3, 0, 0]);
assert.deepEqual(fixture[3].map(row => [row.latitude, row.longitude]), [[0, 0], [0, 0], [0, 0.001]]);
const now = new Date();
const from = new Date(now - 48 * 3600 * 1000).toISOString();
console.log('Fixture checks passed: intervals, distance, zero hours, all map positions.');
for (let i = 0; i < sql.length; i++) {
  const rows = query(sql[i], from, now.toISOString());
  console.log(`${dashboard.panels[i].title}: ${rows.length} rows (last 48h, ais_grafana role)`);
}
