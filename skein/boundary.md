# Boundary

What Skein may settle alone from the decision record, and what always goes to a person.
The team writes and changes this file; Skein only reads it.

Format: two lists in plain words. Anything not covered by either list goes to a person.

Drafted by `skein init` on 2026-10-22: the first list is the topics the drafted decisions cover,
the second is the usual always-a-person list plus anything the drafts flagged. Edit freely.

## Skein may settle

- Review process.
- Testing.
- Version control.
- Pipeline tuning.
- Caching.
- Module boundaries.
- Dependencies.
- Pipeline stages.
- Video encoding.
- Naming.
- Test setup.
- Logging.

## Always a person

- Releases, deploys, and anything that changes what users see in production.
- Deleting or removing anything people depend on: routes, tables, columns, files, data.
- Security: secrets, access, permissions, authentication.
- Customer or personal data: what is collected, stored, logged or shared.
- Money: pricing, billing, payments, spend on services.
- Migrations (from the drafted decisions).
- Uploads (from the drafted decisions).
- Storage (from the drafted decisions).
- Security access (from the drafted decisions).
