# 9. Open source and possible later services

## Today

Tournevia Community is a personal open-source project, licensed under Apache-2.0.
It is the complete planner: road routing, optimization, independent validation, the
dispatcher's map, imports and exports, and the API. There is no paid edition, hosted
service, developer API or support contract, and none is promised.

Running Tournevia needs no subscription or vendor account. Self-hosting is not free
infrastructure, though: operators provide the computer, the road data and the map
and address services they choose, each configurable and replaceable so that no
installation depends on one provider.

## Principles for any later service

If paid services are offered later, they follow these rules:

- **The Community edition stays complete.** The routing model, hard-constraint checks,
  the usable map and ordinary import/export are never weakened or held back to sell
  something else. Correct routes are not a paid feature.
- **Services are separate from the code.** Possible later offers are managed hosting
  of the same planner, a hosted developer API with managed routing capacity, and
  business additions such as ERP connectors, enterprise sign-in, reporting and
  commercial support. Hosting and editions are separate choices: a company can run
  Community without buying hosting.
- **No second core.** There is one solver integration, one validator and one map;
  no separate codebases or general extension framework for editions. Billing,
  quotas or keys, if they ever exist, sit around the core and never change how a
  plan is validated.
- **Standalone first.** Tournevia plans routes without an ERP. Integration with Odoo
  or other systems would be a connector, not a requirement.

None of these services, nor any pricing, metering or billing, exists in the code.
A hosted developer section (documentation, keys, usage and status) would be built
only when such an API is launched.

## Licence

**Apache-2.0** applies to the Community project; the full text is in
[LICENSE](../LICENSE). It permits commercial redistribution and modification under
its conditions, and modified versions need not be published. The other side of
that choice is that anyone, a company included, can offer commercial or closed
modifications under the same terms. [S9](08-source-register.md)

The licence covers material its copyright holder can license. Third-party engines,
dependencies and OpenStreetMap-derived data keep their own licences, and map and
address services have their own terms; see [NOTICE.md](../NOTICE.md).
[S6, S9](08-source-register.md) Contributions are accepted under Apache-2.0 as
[CONTRIBUTING.md](../CONTRIBUTING.md) describes. The Community code stays available
under these terms and will not gain a no-commercial-use restriction.

## Order of work

1. Prove route correctness and quality with real fleets.
2. Make a clean self-hosted installation work end to end: graph preparation, built
   images, first-use setup, migrations, example data, backup and restore, tested on
   a clean machine.
3. Only after useful self-hosted pilots, consider managed hosting of the same core,
   business additions for needs real users have shown, and a hosted developer API
   once integration demand, costs and support expectations are understood. Hosting
   many companies on one installation is a separate security and operations step,
   not something an organization column achieves on its own.
