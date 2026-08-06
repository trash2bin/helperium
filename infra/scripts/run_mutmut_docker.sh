#!/bin/bash
# run_mutmut_docker.sh — entrypoint for Docker mutmut run
set -euo pipefail

CPUS=$(nproc 2>/dev/null || echo 4)
echo "=== Running mutmut (max-children $CPUS) ==="
.venv/bin/python -m mutmut run --max-children "$CPUS"

echo ""
echo "=== Results ==="
.venv/bin/python -c "
import sqlite3, sys
conn = sqlite3.connect('mutmut.sqlite')
total = conn.execute('SELECT COUNT(*) FROM mutation').fetchone()[0]
by_outcome = conn.execute('SELECT outcome, COUNT(*) FROM mutation GROUP BY outcome ORDER BY outcome').fetchall()
print(f'Total mutants: {total}')
for o, c in by_outcome:
    print(f'  {o}: {c}')
k = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%KILLED%'\").fetchone()[0]
s = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%SURVIVED%'\").fetchone()[0]
t = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%TIMEOUT%'\").fetchone()[0]
sk = conn.execute(\"SELECT COUNT(*) FROM mutation WHERE outcome LIKE '%SUSPICIOUS%'\").fetchone()[0]
tested = k + s + t + sk
score = k / tested * 100
print(f'Score: {score:.1f}% ({k}/{tested}) of tested; {total} total')
exit(0 if score >= 65 else 1)
"
