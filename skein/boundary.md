# Boundary

What Skein may settle alone from the decision record, and what always goes to a person.
The team writes and changes this file; Skein only reads it.

Format: two lists in plain words. Anything not covered by either list goes to a person.

Drafted by `skein init` on 2026-10-22, narrow on purpose. The first list holds only the kinds of
convention the drafted decisions cover, and Skein settles them only from confirmed entries.
The second list always holds what follows, whatever the drafts say. A draft covering a topic
never puts it on the first list; the team widens that list, init does not.

## Skein may settle

- Nothing yet: the team fills this in from the confirmed record.

## Always a person

- Tuning, thresholds, and model or detection defaults.
- Dependencies and licensing.
- Migrations, deploys, and anything touching shared infrastructure.
- Security and access.
- Users' data.
- Money.
- Deleting things.
