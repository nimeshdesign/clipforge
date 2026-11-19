# Contributing

Issues and pull requests are welcome.

1. Fork, then `./run.sh` to get a working dev server on http://localhost:8800.
2. Keep changes small and focused; match the existing style (plain Python, no build step for the UI).
3. Never commit anything from `secrets/`, `config.yaml`, `data/` or `clips/`.
4. Run `python -m compileall clipforge` before opening a PR; CI also boots the server.
5. Only add code you wrote or that has a compatible licence, and credit it in `NOTICE.md`.
