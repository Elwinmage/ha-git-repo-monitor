#!/bin/sh
# Run the test suite and print the line coverage (read by check_git_status.sh).
cd "$(dirname "$0")/.." || exit 1
python3 -m pytest -q -p no:cacheprovider >/dev/null 2>&1
grep -o 'line-rate="[0-9.]*"' reports/coverage.xml | head -n 1
