# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub: open this repository's
**Security** tab and choose **Report a vulnerability** (GitHub private vulnerability
reporting). Do not open a public issue, pull request or discussion about a suspected
vulnerability.

Include the affected commit or release kit, steps to reproduce using synthetic data,
and the impact you observed. Never include customer records, credentials, session
cookies or details of a deployment you do not operate. Test only synthetic data or
deployments you are explicitly authorized to test, and please allow time for a fix
before publishing details.

Reports are handled privately. There is no guaranteed response time.

## Supported versions

Tournevia Community is pre-1.0 software. Security fixes are made on the main branch
and released with the next version; earlier versions, and kits or images built from
earlier commits, are not patched.

## Security priorities

Implementation priorities are company ownership checks, named users and secure sessions, CSRF protection, safe imports/exports, secret handling, bounded engine execution and recoverable backups. Keep ordinary password/token protection and useful safe error logs; no planning-history certification system is required.

Do not use real customer data or shared access until the engine/model, authentication, authorization, backup and restore checks in `docs/06-testing-and-operations.md` pass. An operational release also needs clean-install and licensing checks. Automated tests cover the authentication, authorization and recovery behaviour described there, but this document does not establish that an operator's own installation has passed those checks.
