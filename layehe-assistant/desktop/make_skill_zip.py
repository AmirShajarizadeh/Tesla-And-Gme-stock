"""Build layehe-nevisi.zip for upload in Claude Desktop / claude.ai: Settings > Capabilities > Skills."""

import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "skills"
out = Path(__file__).resolve().parent / "layehe-nevisi.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted((root / "layehe-nevisi").rglob("*")):
        if f.is_file():
            z.write(f, f.relative_to(root))
print(out)
