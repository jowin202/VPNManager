# Contributing

Contributions are welcome – bug reports, documentation fixes and code.

- **Bugs**: please include the OPNsense version, the error message from the step
  log or `docker compose logs`, and what you expected. Remove keys, secrets and
  public IP addresses before posting.
- **Code**: see [docs/development.md](docs/development.md). Run
  `python -m pytest` in `backend/` and `npx ng build` in `frontend/` before
  opening a pull request. Changes to the OPNsense interaction should come with a
  test against the simulated OPNsense in `backend/tests/fake_opnsense.py`.
- **New OPNsense releases**: if the API changed, `scripts/discover_schema.py`
  shows the new field names. Never commit its output.
