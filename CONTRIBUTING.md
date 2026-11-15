# Contributing

Thank you for helping. Tournevia is a small project: keep a change focused, show its
behavior with tests, and say what you checked. A normal patch needs no RFC, committee
or separate design document; a material architecture change is recorded briefly once
it is agreed.

## Before you start

- Read the [developer guide](docs/developer/README.md): how the code is organized, the
  rules every change keeps, and recipes for common changes. The numbered documents in
  [docs/](docs/) record the product's scope and design decisions.
- For anything larger than a small fix, open an issue first so the approach can be
  agreed before you spend time on it. The [roadmap](docs/07-deferred-scope.md) says
  what is planned and what each addition must keep correct.
- Questions go in an issue with the **Question** form.

## Set up

To run Tournevia, follow
[first route on your computer](deploy/README.md#first-route-on-your-computer). To work
on the code, [development workflow](docs/developer/development.md) explains running the
backend and frontend separately, the tests, and the suites that need the real routing
engines.

## Before you open a pull request

Run the [checks before every commit](docs/developer/development.md#checks-before-every-commit)
(lint, backend and frontend tests, generated contracts, documentation links, build).
Changes to routing, optimization, validation, edits or the worker also need the
real-engine suites described there. Then check that:

- tests show the new behavior and fail without your change;
- generated contracts are regenerated and committed with it (`scripts/export_contracts.py`,
  `npm run api:generate`), and examples still validate;
- every new interface text and server message has its French translation;
- the data is fictional: never commit real order addresses, driver data, passwords,
  tokens, private routing graphs or customer exports;
- users and operators get a line under "Unreleased" in the CHANGELOG, with any
  migration or upgrade note.

Describe in the pull request the behavior that changed, the commands you ran and their
results, and any material risk or migration note. Do not bundle unrelated cleanups.
Keep original dataset and dependency licence notices.

## Security

Report suspected vulnerabilities privately as described in [SECURITY.md](SECURITY.md),
never in a public issue or pull request.

## Licence of contributions and sign-off

The Community project uses Apache-2.0; see [LICENSE](LICENSE). As section 5 of that
licence says, what you submit is licensed under the same terms. Contributions do not
transfer copyright or grant exclusive rights, and there is no contributor licence
agreement to sign.

Every commit in a pull request carries a sign-off line, which certifies the
[Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote
the change or otherwise have the right to submit it under the project's licence.
`git commit -s` adds the line from your Git name and email:

```
Signed-off-by: Your Name <you@example.org>
```

A GitHub no-reply address is fine. A check on each pull request names commits
without the line; `git commit --amend -s` (one commit) or `git rebase --signoff main`
(several) adds it. Identify any differently licensed third-party material you include.

The core planner stays usable without a vendor subscription, and no hosting or
billing code may change how constraints are validated (see
[section 9](docs/09-open-source-and-paid-plans.md)).

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).
