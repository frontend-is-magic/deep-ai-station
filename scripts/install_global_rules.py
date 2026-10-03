"""Install the user-requested global rules; keep a recoverable backup."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = Path("/Users/bloodymoon/.codex")
backup = target / "AGENTS.md.backup-deep-ai-station-20261003"
if not backup.exists():
    shutil.copyfile(target / "AGENTS.md", backup)
(target / "references").mkdir(exist_ok=True)
shutil.copyfile(root / "docs/global-AGENTS.proposed.md", target / "AGENTS.md")
shutil.copyfile(root / "docs/human-control.md", target / "references/human-control.md")
print("Global AGENTS.md and human-control reference installed; previous rules backed up.")
