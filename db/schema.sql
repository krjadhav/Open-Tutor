-- Open Tutor schema. Postgres 15+, targeting Supabase.
--
-- THE ONE RULE: `attempts` is truth, `node_state` is derived.
--
-- Every belief this system holds about a student is a fold over the `attempts` log. `node_state`
-- is a cache of that fold and nothing more: drop it, replay the log through engine/replay.py, and
-- you have it back exactly. Nothing may ever write a mastery number that is not reproducible that
-- way, and nothing may ever read `node_state` and treat a disagreement with the log as the log's
-- problem.
--
-- The reason is commercial, not aesthetic. We will retune LAMBDA_BLAME, STABILITY_GROWTH, the
-- implicit-credit discount and the mastery thresholds many times, and the only honest evidence
-- about whether a change helped is real users' attempts replayed under both settings. In-place
-- mutation folds each attempt away at the constants that happened to be live that day and throws
-- the raw event out, so that comparison becomes impossible forever, not merely inconvenient. The
-- append-only trigger below exists because a rule this load-bearing cannot be left to a comment
-- and good intentions. See learning-design.md section 12.1.
--
-- Apply with: psql "$DATABASE_URL" -f db/schema.sql   (see db/README.md)


-- --------------------------------------------------------------------------- extensions

create extension if not exists pgcrypto;   -- gen_random_uuid()


-- --------------------------------------------------------------------------- static course data
-- nodes/edges/items are per-course and identical for every student. They are content, not state.

create table if not exists nodes (
    id            text primary key,                       -- e.g. 'calc.chain-rule'
    title         text not null,
    kind          text not null default 'skill'
                    check (kind in ('skill', 'concept')),
    difficulty_b  double precision not null default 0.0,   -- Rasch-style topic difficulty
    goal_tags     text[] not null default '{}',            -- 'gradient-descent', 'jee-mains', ...
    blame_hint    text,                                    -- PROMPT-ONLY, never shown to a user
    created_at    timestamptz not null default now()
);

comment on column nodes.blame_hint is
    'Disambiguation text injected into the diagnosis prompt so blame lands on the right node '
    '(broad titles otherwise attract blame belonging elsewhere). Internal: never render it in '
    'the UI and never translate it.';

comment on column nodes.difficulty_b is
    'Used with node p_eff to predict success: P(correct) = sigmoid(logit(p_eff) - b_item).';


create table if not exists edges (
    from_node  text not null references nodes (id) on delete cascade,
    to_node    text not null references nodes (id) on delete cascade,
    kind       text not null check (kind in ('prereq', 'encompass')),
    weight     double precision not null default 1.0,
    primary key (from_node, to_node, kind),
    check (from_node <> to_node)
);

comment on table edges is
    'Direction by kind, and the two are NOT interchangeable. prereq: from_node must be known '
    'BEFORE to_node (weight = how load-bearing, 0..1). encompass: solving from_node exercises '
    'component skill to_node (weight = implicit credit, ~0.2..0.4, per FIRe). Prereqs are what '
    'you needed before; encompassings are what you actually used during, and only encompassings '
    'earn implicit credit. The prereq subgraph must stay a DAG; that is validated on ingest '
    'because a cycle here deadlocks the frontier.';


create table if not exists items (
    id            text primary key,
    node_id       text not null references nodes (id) on delete cascade,
    stem_latex    text not null,
    answer_latex  text,
    answer_sympy  text,                                   -- what the CAS grader actually parses
    answer_kind   text check (answer_kind in ('expr', 'set', 'vector')),
    difficulty_b  double precision not null default 0.0,
    encompasses   text[] not null default '{}',           -- component skills this item exercises
    source        text not null default 'generated'
                    check (source in ('openstax', 'generated', 'original')),
    created_at    timestamptz not null default now()
);

comment on column items.source is
    'Licence provenance, and it is a commercial kill switch, not metadata. openstax = CC BY-NC-SA '
    '4.0: fine for a non-commercial hackathon with attribution, and ILLEGAL to ship in a paid '
    'product because of the NC clause. generated/original = ours, safe to sell. When we '
    'commercialise we delete where source = ''openstax'' and regenerate, which is a query rather '
    'than a re-architecture. That only works if this column is populated from the first row '
    'inserted. See learning-design.md section 12.2.';

comment on column items.answer_kind is
    'sympy answers do not round-trip through str(): a solution set prints as {2, 3} and a '
    'gradient as Matrix([[..]]), neither of which parses back. The kind tells the grader how the '
    'answer was serialised.';

create index if not exists idx_items_node on items (node_id);
create index if not exists idx_items_node_source on items (node_id, source);   -- licence sweeps


-- --------------------------------------------------------------------------- students

create table if not exists students (
    id          uuid primary key default gen_random_uuid(),
    goal_node   text references nodes (id) on delete set null,   -- what they said they want
    lang        text not null default 'en',                      -- 'en' | 'hi'
    created_at  timestamptz not null default now()
);


-- --------------------------------------------------------------------------- THE LOG

-- The source of truth. One row per thing the student actually did. Append only.
create table if not exists attempts (
    id                 uuid primary key default gen_random_uuid(),
    student_id         uuid not null references students (id) on delete restrict,
    item_id            text references items (id) on delete set null,
    node_id            text not null references nodes (id) on delete restrict,
    ts                 timestamptz not null default now(),
    correct            boolean not null,
    answer_given       text,
    hint_level         smallint not null default 0 check (hint_level between 0 and 4),
    channel            text not null default 'typed'
                         check (channel in ('photo', 'typed', 'mcq')),
    photo_url          text,                    -- Supabase Storage object path, not a blob
    ocr_text           text,
    used_nodes         text[] not null default '{}',    -- component skills the solution used
    blamed_node        text references nodes (id) on delete set null,
    blame_confidence   double precision not null default 0.0,
    misconception_tag  text,
    diagnosis_json     jsonb,                   -- full model output, kept verbatim for retuning
    latency_ms         integer
);

comment on table attempts is
    'APPEND ONLY, enforced by trigger below. This table is the entire memory of the product; '
    'everything else about a student is recomputable from it and nothing else is.';

comment on column attempts.ts is
    'When the attempt happened. Called created_at in learning-design.md section 12.1 and mapped '
    'to Attempt.ts by the engine. Spacing between rows drives consolidation and decay, so this '
    'is model input, not an audit column.';

comment on column attempts.node_id is
    'The node the ITEM was tagged to. May differ from blamed_node, which is where the failure '
    'was actually routed. Keeping both is what lets us measure diagnosis accuracy later.';

comment on column attempts.blame_confidence is
    'Logged, never used as a gate. Experiment 2 found the model''s stated confidence is not '
    'separable between right and wrong diagnoses (0.97 vs 0.93), so gating on it would be '
    'superstition. It is kept because a future model may make it informative.';

comment on column attempts.diagnosis_json is
    'The raw diagnosis payload. Stored whole and unparsed on purpose: when we change how blame '
    'is interpreted we re-derive from this rather than re-running the model over old work, which '
    'we could not afford and could not reproduce.';

-- Access patterns:
--   replay one student's whole history in time order  -> (student_id, ts)
--   evidence for one student on one node              -> (student_id, node_id)
create index if not exists idx_attempts_student_ts on attempts (student_id, ts);
create index if not exists idx_attempts_student_node on attempts (student_id, node_id);
create index if not exists idx_attempts_student_blamed on attempts (student_id, blamed_node)
    where blamed_node is not null;                  -- "what is blocking me" / remediation slot


-- Append-only enforcement.
--
-- Not documentation, a wall. An UPDATE that "just fixes" a correct flag or a DELETE that "just
-- cleans up test data" silently changes history, and every mastery number derived from it becomes
-- unreproducible with no error and no trace. Correcting an attempt means appending a new row, the
-- same as every other event.
create or replace function attempts_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception
        'attempts is append-only: % is not permitted. Append a corrective row instead; see '
        'db/README.md for the deliberate, audited escape hatch (erasure requests only).',
        tg_op
        using errcode = '0A000';   -- feature_not_supported
end;
$$;

drop trigger if exists attempts_no_update on attempts;
create trigger attempts_no_update
    before update on attempts
    for each row execute function attempts_append_only();

drop trigger if exists attempts_no_delete on attempts;
create trigger attempts_no_delete
    before delete on attempts
    for each row execute function attempts_append_only();

-- Note the knock-on: students.id and nodes.id are referenced ON DELETE RESTRICT, not CASCADE. A
-- cascade would try to delete attempts and hit the trigger, failing the whole transaction with a
-- confusing error. Deleting a student is therefore an explicit, logged procedure (README), which
-- is the correct amount of friction for an operation that destroys the training signal.


-- --------------------------------------------------------------------------- DERIVED CACHE

-- Derived. Not truth. Rebuildable in full by replaying `attempts` through engine/replay.py.
--
-- It exists only so the daily-set endpoint does not refold a student's entire history on every
-- request. Anything in here may be deleted at any time; anything that CANNOT be reconstructed
-- from `attempts` does not belong in here. If this table and the log ever disagree, the log is
-- right by definition and this table is stale.
create table if not exists node_state (
    student_id      uuid not null references students (id) on delete cascade,
    node_id         text not null references nodes (id) on delete cascade,
    a               double precision not null default 1.0,   -- Beta posterior; a=b=1 is "unknown"
    b               double precision not null default 1.0,
    stability       double precision not null default 1.0,   -- days; memory half-life
    last_seen       timestamptz,
    speed           double precision not null default 1.0,   -- per-student-per-topic multiplier
    successes       integer not null default 0,              -- spaced successes, mastered gate
    misconceptions  text[] not null default '{}',            -- open tags, oldest first
    replayed_upto   timestamptz,                             -- last attempt folded in
    updated_at      timestamptz not null default now(),
    primary key (student_id, node_id)
);

comment on table node_state is
    'DERIVED CACHE, never the source of truth. A materialized fold of `attempts`; truncate it and '
    'engine/replay.py reproduces it exactly. Status (locked/frontier/learning/mastered), '
    'retrievability and p are NOT stored here: they are computed from these columns plus the '
    'thresholds in engine/types.py, precisely so that retuning a threshold does not require a '
    'migration.';

comment on column node_state.replayed_upto is
    'Watermark: the timestamp of the newest attempt folded into this row. Lets a writer detect a '
    'stale cache and refold, and makes an incremental fold safe to resume.';

-- The primary key (student_id, node_id) already serves lookups by student_id as a prefix, so the
-- "node_state by student_id" access pattern (load a whole student's graph) needs no second index.


-- --------------------------------------------------------------------------- daily sets

create table if not exists daily_sets (
    id              uuid primary key default gen_random_uuid(),
    student_id      uuid not null references students (id) on delete cascade,
    set_date        date not null,
    item_ids        text[] not null default '{}',    -- ordered: remediation first, interleaved
    rationale_json  jsonb,                           -- why each slot was filled the way it was
    created_at      timestamptz not null default now(),
    unique (student_id, set_date)
);

comment on column daily_sets.rationale_json is
    'Per-item {slot, reason, predicted_success}. Not debug output: the reason is shown to the '
    'student ("Blocker: sign distribution"), which is one of the few places the system''s '
    'reasoning becomes legible. Also the only way to audit a selection after the fact, since '
    'selection depends on state that has since moved on.';

create index if not exists idx_daily_sets_student_date on daily_sets (student_id, set_date desc);


-- --------------------------------------------------------------------------- i18n

create table if not exists translations (
    entity_type  text not null check (entity_type in ('node', 'item', 'hint', 'ui')),
    entity_id    text not null,          -- nodes.id, items.id, or a UI string key
    lang         text not null,          -- 'hi', ...
    field        text not null default 'text',   -- which field of the entity: title, stem, ...
    text         text not null,
    created_at   timestamptz not null default now(),
    primary key (entity_type, entity_id, lang, field)
);

comment on table translations is
    'Translations are stored, never generated per request: a re-translation that reflows LaTeX '
    'must be caught once at ingest, not silently on a student''s screen. Math is templated out '
    'before translation and substituted back after (learning-design.md section 13), so the text '
    'here contains placeholders, not raw LaTeX.';

-- Lookup is always (entity_type, entity_id, lang); the primary key above IS that index, with
-- `field` as a trailing tiebreak, so a separate index would be redundant.
