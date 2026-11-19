"""Entry point: one read-only MCP server for Iranian laws (qavanin.ir) and court rulings (ara.jri.ac.ir)."""

from . import ara, qavanin  # noqa: F401  (importing registers the tools)
from .app import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
