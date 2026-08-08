# Database

Postgres, targeting Supabase. Eight tables, one file: `schema.sql`.

## Apply to a fresh Supabase project

1. Create the project (any region). Note the database password.
2. Project settings, Database, Connection string, URI. Copy it.
3. Run the schema:

   ```sh
   psql "postgresql://postgres:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres" -v ON_ERROR_STOP=1 -f db/schema.sql
   ```

   Without `psql`, paste `schema.sql` into the dashboard SQL Editor and run it. With the Supabase
   CLI, copy the file into `supabase/migrations/<timestamp>_init.sql` and `supabase db push`.

4. Load content: nodes and edges from `data/graph/nodes.json`, items from `data/items/items.json`.

The script is idempotent (`create table if not exists`, `create or replace function`,
`drop trigger if exists`), so re-running it on an existing database is safe and changes nothing.
It does not enable row-level security; do that before any client key touches the project, with a
policy per table keyed on `auth.uid() = student_id`. Handwriting photos go in Supabase Storage and
`attempts.photo_url` holds the object path, never the image itself.

## Why attempts is an event log and node_state is derived

`attempts` is the source of truth and is append only. `node_state` is a cache of a fold over it:
delete the whole table and `engine/replay.py` reproduces it exactly, row for row.

That is not tidiness, it is the only thing that keeps the product tunable. We are going to change
`LAMBDA_BLAME`, `STABILITY_GROWTH`, the implicit-credit discount and the mastery thresholds many
times, and the only honest evidence about whether a change helped is our real users' attempts
replayed under the old constants and the new ones and compared. That works because the raw events
survive. If we instead mutated mastery in place, every attempt would have already been folded away
at whatever constants were live that day and the event itself discarded: the comparison becomes
impossible permanently, not merely awkward, and we would be tuning the core of the product blind
on the only data that could ever tell us the truth. It costs nothing extra to build this way now
and cannot be retrofitted later, because the history you would need was never written down.

So: never write a number into `node_state` that a replay would not produce. Never repair an
attempt with an `UPDATE`. A wrong attempt is corrected by appending a new one, exactly like every
other event. A trigger enforces both, because a rule this load-bearing does not survive as a
comment.

## The escape hatch, and when it is legitimate

Exactly one situation justifies removing rows from `attempts`: a verified erasure request. It is
deliberately awkward, and it is meant to be done by a human in a transaction:

```sql
begin;
alter table attempts disable trigger attempts_no_delete;
delete from attempts where student_id = '...';
alter table attempts enable trigger attempts_no_delete;
delete from node_state where student_id = '...';
delete from students where id = '...';
commit;
```

Foreign keys from `attempts` to `students` and `nodes` are `on delete restrict` rather than
`cascade` for this reason: a cascade would silently try to delete attempts, hit the trigger, and
abort the transaction with an error that points at the wrong table. Making erasure explicit is the
right amount of friction for an operation that destroys signal we cannot regenerate.

## Column notes worth knowing before you write a query

- `attempts.ts` is what section 12.1 of the learning design calls `created_at` and what the engine
  reads as `Attempt.ts`. The spacing between rows drives consolidation and decay, so it is model
  input, not an audit column.
- `attempts.node_id` is the node the *item* was tagged to; `attempts.blamed_node` is where the
  failure was routed. They differ often and on purpose, and keeping both is how we will measure
  diagnosis accuracy.
- `items.source` is a licence kill switch. `openstax` content is CC BY-NC-SA 4.0: fine for a
  non-commercial hackathon with attribution, and illegal to ship in a paid product. When we
  commercialise, we delete `where source = 'openstax'` and regenerate. That only works if the
  column is right from the first insert.
- `nodes.blame_hint` is prompt-only disambiguation for the diagnosis call. Never render it and
  never translate it.
- `edges.kind` distinguishes `prereq` (needed before, `from_node` is the prerequisite) from
  `encompass` (exercised during, `from_node` is the larger skill). Only `encompass` earns implicit
  credit. The prereq subgraph must remain a DAG.
- Status, retrievability and `p` are never stored. They are derived from `node_state` plus the
  thresholds in `engine/types.py`, so retuning a threshold is not a migration.
