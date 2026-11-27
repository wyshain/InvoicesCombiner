"""Export daily totals from the old combined output directory."""
import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()

counts = Counter()
for path in args.directory.glob("combined_*.pdf"):
    match = re.fullmatch(r"combined_(\d{8})_\d{6}\.pdf", path.name)
    if match and path.is_file() and path.stat().st_size:
        day = datetime.strptime(match[1], "%Y%m%d").date().isoformat()
        counts[day] += 1
print(json.dumps(counts, sort_keys=True))
