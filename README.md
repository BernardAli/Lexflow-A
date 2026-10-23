# LexFlow AI — dedicated-office pilot prototype

LexFlow demonstrates property-purchase intake for a dedicated notary office:
receive digital PDFs/text, scanned PDFs/images and saved email, propose values with source evidence,
resolve conflicts, approve an immutable snapshot, create an export, confirm its
receipt and retain an auditable history.

The demonstration uses synthetic records and an independent HTTP reference
receiver (or the earlier XML file receiver). Local PostgreSQL concurrency,
authenticated handoff, remote intake, encrypted recovery and extraction have
reproducible acceptance evidence. A client's supported destination, hosted
deployment and office measurements still require acceptance.

## Start the presentation

The existing virtualenv is sufficient. For a new checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then run:

```sh
.venv/bin/python tools/presentation_demo.py --http-handoff
```

Open http://127.0.0.1:8000/presentation/. Accounts `keller.clerk`, `keller.notar`
and `admin` use the synthetic demo password `lexflow-demo-2026`.

The launcher creates `.pilot-demo/` with a separate one-office database, encrypted
vault, export folder and unique local keys. It does not use the project's existing
`db.sqlite3` or `vault/`. Use `--port 8010`, `--prepare-only` or `--reset` as needed;
reset archives the earlier synthetic demo under `/tmp`.

The HTTP option starts a separate synthetic-only receiver process/database.
After approval, choose **HTTP reference receiver**, create its export, then send
it and show the verified receipt. Without the option, use the XML file receiver.
Local OCR is automatically enabled when Tesseract is available (including this
workspace's ignored `.pilot-tools/` runtime); the presenter guide then links a
scanned example. On another host install Tesseract, German/English language packs
and Poppler, or explicitly set `LEXFLOW_OCR_ENABLED=0` for manual scan review.

`presentation/` contains an editable 12-slide PowerPoint, a matching PDF, reusable
slide text, a timed presenter script and verification notes. Rebuild the slides
with `.venv/bin/python tools/build_pilot_presentation.py`. The generator uses the
original client deck's theme; keep that referenced source file available.

## Password reset and visibility

The eye button shows or hides a password on sign-in and new-password forms.
Choose **Forgot password?** on sign-in and enter the email registered to the
account. Reset links expire after one hour and cannot be reused after a successful
reset. The request page gives the same confirmation for unknown/ineligible
addresses. Existing signed-in sessions expire when their old password hash is
next checked.

Development uses Django's console email backend: the reset email appears in the
server terminal instead of being delivered to an inbox. Demo addresses include
`keller.clerk@example.test` and `keller.notar@example.test`. For deployed offices,
set each user's email in Django admin and configure SMTP using the email settings
in `deployment/pilot.env.example`. `DJANGO_EMAIL_BACKEND` can override the backend;
SMTP supports `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`EMAIL_USE_TLS` or `EMAIL_USE_SSL`, and `DEFAULT_FROM_EMAIL`. Validate delivery with
the office's mail service before relying on self-service recovery.

## Implemented pilot controls

- Review, classification, re-extraction, approval and export serialize on the
  office/matter in a consistent lock order. Mutation guards reload current state.
  PostgreSQL is required for live deployment row-lock guarantees; the local demo
  uses SQLite and is not a concurrent production deployment.
- Approval freezes the values, destination mappings and approval identity in an
  encrypted snapshot. Its digest is bound to the signed audit chain. Changes in
  the studio do not change an existing approved export. Overlapping destination
  paths are rejected. The GmbH starter schema uses a nested director name/date.
- Source quotes absent from a document are discarded. Conflicting values within
  a document remain conflicts. Duplicate source content cannot inflate confidence.
- Source selections fill case data only. Administrators explicitly configure
  reusable schema labels, mappings and validation policy in the studio.
- Digital PDF/text processing and optional Tesseract OCR run locally. Scanned
  PDFs/images are read within page/pixel/subprocess limits. Mixed PDFs do not hide
  unread pages. Failed/partial OCR retains the original for manual review. Every
  scan, including successful OCR, needs original-source acknowledgement before
  approval. OCR output is evidence for review, not a calibrated accuracy score.
- Source files/text, structured case values, revisions, source metadata, approval
  snapshots and audit metadata use the owning office's encryption key. Sensitive
  database fields cannot be searched or updated as plaintext SQL values. Bounded
  pilot searches decrypt tenant-scoped results in the application.
- Exports are `exported` or `pending` until an approver confirms a matching
  checksum and destination receipt. Folder exports are written atomically with
  restricted permissions. Repeated requests for one approval/connector reuse an
  idempotency key. The simulated receiver only accepts synthetic demo matters.
- Authenticated HTTP handoff sends approved JSON to an operator-configured HTTPS
  endpoint. Requests and receipts use separate HMAC domains and bind exact bytes,
  approval, transfer and idempotency key. A timeout stays pending; retry retrieves
  the receiver's original durable receipt. A typed reference cannot bypass signed
  HTTP receipt verification. See `integrations/HANDOFF_V1.txt`.
- Audit events reject instance and bulk mutations. Verification checks the chain
  against an office-key-signed sequence/head checkpoint, detecting tail removal
  as well as middle deletions. Verification serializes against concurrent appends.
  `lexflow_audit_checkpoint` exports a signed anchor for separate operator custody;
  comparing with a trusted latest anchor detects replay of an older valid database.
  The operator must independently protect anchors; LexFlow does not provision WORM.
- Retention begins after acknowledged delivery. Cleanup clears source text and
  the original email body, preserves active file references and queues failed
  blob deletions for retry. Structured case/review history, approved exports and
  audit records remain; agree their separate retention policy with the office.
- Intake validation is shared across web, API and folder intake. Size/count/total
  limits, empty inputs, unsupported types and malformed PDFs produce actionable
  errors. Provider failures retain originals for retry. External intake IDs are
  unique per office and repeated submissions reuse the stored intake.
- `tools/scanner_agent.py` runs remotely with only an intake token and HTTPS
  access. It handles scanner files and MIME-decoded saved email/attachments,
  tracks confirmations privately and deduplicates retries even after state loss.
- `lexflow_backup` and `lexflow_restore` provide authenticated encrypted offline
  snapshots with a separate recovery key. Fresh-target restore checks source
  bytes, structured private fields, approvals, exports and the signed audit state.
  Stop/drain all writers first; see `deployment/OPERATIONS.txt`.

## Verification

```sh
.venv/bin/python manage.py test apps
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python -m pip check
```

The regression suite covers the original review failures, actual folder-export
bytes, receipt validation, tenant/role gates, raw database encryption and demo
isolation. `lexflow_pilot_check --demo --json` checks the isolated synthetic demo;
without `--demo` it fails closed on unmet live-pilot conditions.

The original `PROJECT_REVIEW.txt` and `review/` files are a dated pre-hardening
baseline. Their diagnostic script illustrates old failure paths; use the current
regression tests and `presentation/VERIFICATION.txt` for the updated build.

The PostgreSQL suite runs real concurrent approval/edit, export, intake and audit
checks; those tests deliberately skip on SQLite. Reproduce the complete synthetic
drill (OCR enabled) with:

```sh
.venv/bin/python tools/verify_pilot_acceptance.py --output /tmp/sqlite-drill.json
# With an isolated PostgreSQL TEST server configured through POSTGRES_*:
.venv/bin/python tools/verify_pilot_acceptance.py --postgres --output /tmp/postgres-drill.json
.venv/bin/python manage.py lexflow_evaluate --tenant keller --dataset evaluation/property_cases.json
```

The drill creates fresh databases/storage and checks actual HTTP, an independent
receiver, remote saved-email intake, encrypted restore and extraction. It never
drops existing databases. The supplied corpus has 12 synthetic cases and 64
explicit checks. It is regression evidence, not representative office accuracy.
Evaluation can use sanitized files referenced by relative paths in a local
version-1 dataset; reports omit document text and extracted values.

## Existing development database

Back up the database together with its vault and matching master key before
migrating. The migration encrypts legacy case fields and seals only an audit chain
that already verifies. Earlier local exports are relabelled `exported`; earlier
approvals must be reopened/reviewed and approved with a frozen snapshot before a
new export. Existing case records and exports are preserved.

```sh
.venv/bin/python manage.py migrate
```

For a legacy SQLite database, compact it after migration to remove old freed-page
plaintext. Securely retire plaintext-era backups under the office's retention
policy; field encryption cannot retroactively protect those backups.

```sh
.venv/bin/python manage.py shell -c "from django.db import connection; connection.cursor().execute('VACUUM')"
```

## Dedicated live deployment template

`deployment/` supplies a dedicated PostgreSQL/application/Caddy HTTPS stack. It
must be built and validated by the operator; Docker, cloud infrastructure and a
real vendor were not available in this workspace. PostgreSQL and recovery were
tested locally; those checks do not validate the hosted stack. Give each office its own
stack, database credentials, vault/export volumes and master key. Do not seed a
live deployment with demonstration data.

1. Copy `deployment/pilot.env.example` to `deployment/pilot.env` with mode 0600.
   Supply a unique Django secret of at least 50 characters, `fernet:<key>` master
   key, office slug, actual hostname, allowed hosts and operator-confirmed region.
   Set `POSTGRES_PASSWORD` and `LEXFLOW_PUBLIC_HOST` in the operator environment.
2. Build the images. Run migrations and `collectstatic` with the configured secret
   environment before starting the stack. Caddy owns the public HTTP/HTTPS ports;
   app and database ports are private. `DJANGO_TRUST_PROXY=1` is only appropriate
   behind this controlled proxy, which sets the forwarded scheme.
3. Set `LEXFLOW_ADMIN_PASSWORD` in the operator's secret environment, and run
   `provision_office --name "Office name" --admin office.admin`. This creates one
   office and named administrator without demo data or a public demo password.
   Add separate reviewer/notary accounts and memberships using approved operator
   procedures; configure the supported import format or use
   `configure_http_connector` with an operator endpoint key and private secret.
4. Validate destination schema/import and receipt against sanitized acceptance
   cases. Record the office baseline. Run `lexflow_pilot_check --json` and
   `check --deploy`; investigate any failed condition before live use.
5. Test deployment concurrency, key provisioning, volume permissions, TLS, backup
   and restore, audit recovery, retention, monitoring and operator access in the
   actual environment. Have the office accept the exact scope and controls.

Production startup refuses missing/development secrets, invalid master keys,
active demo mode or an unspecified dedicated office. Profile labels do not
provision cloud infrastructure or certify residency/compliance.

## Useful commands and boundaries

```sh
.venv/bin/python manage.py lexflow_folder_agent --tenant keller --path ./scanner-inbox
.venv/bin/python manage.py lexflow_purge --dry-run
.venv/bin/python manage.py lexflow_purge
```

The legacy folder command is operator-side and needs database/key access; use
`tools/scanner_agent.py` for the remote thin client. Native Outlook attachment
selection, Word/DMS verification, SSO, billing and additional hosting profiles
remain later scope. XML and HTTP JSON use documented LexFlow formats. Neither is
a vendor/BNotK schema, and the reference receiver is not a vendor connection.

The token API offers `POST /api/v1/intake/` and tenant-scoped
`GET /api/v1/matters/<ref>/`. It exposes explicit blockers and approval/export
readiness. An office token permits intake/read operations; it does not approve
matters. External AI remains opt-in per office and disabled in the presentation
launcher. No live external provider was needed for the demonstration.
