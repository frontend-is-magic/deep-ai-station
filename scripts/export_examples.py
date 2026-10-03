"""Export committed curriculum code for syntax validation, without executing it."""

import ast
import json
import sys
from pathlib import Path

from backend.curriculum import LESSONS

examples = [
    {"id": lesson["id"], "language": language, "source": source}
    for lesson in LESSONS.values()
    for language, source in lesson["snippets"].items()
]
for item in examples:
    if item["language"] == "python":
        ast.parse(item["source"], filename=item["id"])
Path(sys.argv[1]).write_text(json.dumps(examples, ensure_ascii=False))
print(f"Exported {len(examples)} references; Python AST checked")
