# Files

- [MCP Server and AI Agent Integration](mcp-server.md) - The stdio MCP tool surface in docsweep/mcp_server.py, how each tool maps onto the same core engine and services the CLI uses, and the CLI-first philosophy for AI agents that skip MCP entirely.
- [OKF (Open Knowledge Format) Compatibility](okf-compatibility.md) - How docsweep's own frontmatter fields map onto OKF v0.2's status/type model, the version-profile mechanism behind okf-check and export --okf, and the Bundle export format.
- [AI Authorship and Execution Provenance](provenance.md) - The opt-in system that records which AI created a work document and which AI executed each context section, its external CSV ledger, session-transcript capture, and the manager repo/docsweep split.
- [Web UI (FastAPI Board)](web-ui.md) - The local FastAPI + htmx kanban board — app wiring, token-based auth, path-scope enforcement, and the strict CSP/sanitization invariants that keep it safe to preview untrusted Markdown.
