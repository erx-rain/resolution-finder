# Resolution Finder

Proposes resolutions for RainTrade prediction markets — whether a
resolution is available, and what the market resolves to, with evidence —
for a human to confirm. Local, rule-based + small NLI model, $0 to run.

- **Start here:** [`docs/HANDOFF.md`](docs/HANDOFF.md) — the complete handoff
  (what it is, setup, architecture, data, eval, strengths/weaknesses, roadmap).
- **Using Claude Code?** [`CLAUDE.md`](CLAUDE.md) loads automatically and
  points to the handoff.

Quick start (Linux / WSL):

```bash
python3 -m venv .venv-wsl && .venv-wsl/bin/pip install -r requirements.txt
PYTHONPATH=. .venv-wsl/bin/python -m pytest tests/ -q
```
