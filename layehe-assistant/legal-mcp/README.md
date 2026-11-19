# legal-mcp

Read-only MCP server for Iranian law and case law, through a local headless browser (Playwright, using the
installed Chrome or Edge when available):

- qavanin.ir (سامانه ملی قوانین و مقررات): `law_search`, `law_read`, `law_info`, `law_amendments`,
  `article_history`, `law_related`
- ara.jri.ac.ir (سامانه ملی آرای قضایی): `ara_search`, `legal_read`
- `ara_diagnose`: checks that the browser starts and the sites answer, and saves their HTML

Run with `uv run legal-mcp`. See the parent README for setup.
