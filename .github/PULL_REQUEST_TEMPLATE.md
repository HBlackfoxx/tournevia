## What changes

<!-- The behavior that changes, for whom, and why. Link the issue if there is one. -->

## Checks run

<!-- The commands you ran and their result. See docs/developer/development.md#checks-before-every-commit. -->

- [ ] `python -m ruff check .`
- [ ] `python scripts/test_backend.py` (or the plain pytest command, saying which tests were skipped)
- [ ] `python scripts/export_contracts.py --check` and, in `web/`, `npm run api:check`
- [ ] In `web/`: `npm run format:check`, `npm run lint`, `npm test`, `npm run build`
- [ ] `python scripts/validate_pack.py` (documentation links, contract files, examples)
- [ ] Real-engine suites, if routing, optimization, validation, edits or the worker changed

## Checklist

- [ ] Tests show the new behavior, and fail without the change.
- [ ] Generated contracts are regenerated and committed with the change.
- [ ] Every new interface text and server message has its French translation.
- [ ] Fictional data only; no real addresses, credentials, tokens, graphs or exports.
- [ ] Migration or upgrade notes, and a CHANGELOG line for changes users or operators will notice.
- [ ] Every commit is signed off (`git commit -s`): I can submit this work under Apache-2.0, as CONTRIBUTING.md describes.
