# The Core Engine

The core engine is everything between curriculum content and a student's next task: a knowledge graph (static, shared by everyone), a student model (dynamic, per student), and the algorithms that join them. This document defines the objects, the invariants they obey, and the algorithms that run on them, in that order.


```
            static, shared                      per student
    +--------------------------+      +--------------------------+
    |      knowledge graph     |      |       student model      |
    |  topics, points, edges,  |      |  reps, schedules, speeds,|
    |  weights, key prereqs    |      |  evidence, halt counts   |
    +------------+-------------+      +------------+-------------+
                 |                                 |
                 +----------------+----------------+
                                  |
          diagnostic | task selection | remediation | quizzes
```

## 1. The knowledge graph

### 1.1 Topics

Definition (topic). A node of the graph: one lesson's worth of mathematics, and the unit at which lessons are served, mastery is tracked, and review is scheduled. A topic has a stable id (never reused after deletion or splitting, since student state is keyed on it), a curriculum-unique name, placement metadata (grade band, strand), and membership in at least one course.

Definition (knowledge point). A topic's internal steps: an ordered chain, each point holding one worked example and a pool of interchangeable questions of the same type, sequenced from the basic case upward with non-decreasing intended difficulty. Mastering a topic means answering sufficiently many questions correctly in each successive point.

Definition (difficulty). Every question carries a difficulty value, at minimum ordinal within its topic and preferably comparable across topics, since quizzes target an expected score near 80 percent and trade difficulty across topics to hit it. The easiest and hardest variation of each topic is thereby recorded. Calibration is an open parameter; item response theory is a candidate.

Axiom (pool size). Every pool holds at least Q_MIN distinct questions. Mastery checks, retries after failure, and months of spaced review all draw fresh questions from the same pools.

### 1.2 Granularity

Dependency structure does not stop at the topic: within a lesson each point builds on the previous one, and points also use skills taught in other lessons.

Definition (fine graph). F has all knowledge points as vertices, with two edge kinds:

```
chain edges   kp(t, i) -> kp(t, i+1)
cross edges   kp(u, j) -> kp(t, i)
              "point i of t uses a skill taught at point j of u"
```

Definition (quotient). The topic graph is the quotient of F: collapse each topic's chain to a single vertex, with u -> v in P exactly when a cross edge runs from inside u into v. The knowledge point is the atomic unit of dependency; the topic is the atomic unit of the graph.

Proposition (lossless collapse). Collapsing costs the algorithms nothing. The collapsed chain is served, mastered, and reviewed as one unit, so per-point mastery and schedule state would be state nothing acts on; the chain contributes only path-shaped structure; and the cross edges, the part of F the algorithms consume, survive as key-prerequisite links. Promoting every point to a node would multiply node count, authoring surface, and per-student state by the chain length and buy nothing the scheduler can use.

Proposition (interleaving). The quotient is acyclic exactly when dependencies never interleave: no point of v needs an early point of u while a later point of u needs v. Interleaving means no correct topic-level edge set exists for the current partition, and a split is mandatory.

In practice the node line sits low. Adding Fractions With Unlike Denominators Using Models and its abstract counterpart are separate topics, not two points of one fat topic, because their dependency profiles differ. What stays inside a topic is the residue: a short chain of escalating cases with identical external dependencies.

### 1.3 Splitting

Definition (sequential split). For a topic too long or too hard as one sitting: cut the chain, and the first half becomes a prerequisite of the second.

```python
def split_sequential(t: Topic, j: int) -> tuple[Topic, Topic]:
    """kp[0:j] becomes t1, kp[j:] becomes t2, with t1 -> t2 in P.
    The id t is retired; a mapping {t: (t1, t2)} is published so the
    student model can migrate per-student state (policy: open parameter)."""
    t1 = new_topic(t.knowledge_points[:j])
    t2 = new_topic(t.knowledge_points[j:])
    add_prereq(t2, t1.id)
    for u in G.prereq.pop(t.id):               # incoming edges
        for half in (t1, t2):
            if uses(half, u):                  # per the step analysis
                add_prereq(half, u)
    for v in consumers_of(t):                  # outgoing edges
        add_prereq(v, t2.id if needs_second_half(v) else t1.id)
    rederive_encompassings(t1)                 # weights are per half now
    rederive_encompassings(t2)
    G.prereq = transitive_reduction(G.prereq)  # removes shortcuts
    validate(G)
    return t1, t2
```

Incoming edges attach to each half that uses them; re-reduction deletes what became redundant. Consumers depended on all of t, so they attach to t2 unless the step analysis shows they need nothing from the second half. Key-prerequisite links travel with their points, and points of t2 may now name t1.

Definition (parallel split). For a node conflating two independent skills: partition the points into independent groups, create sibling nodes with no edge between them, reassign incoming and outgoing edges per actual use, re-reduce, validate.

Axiom (split contract). A split retires the old id, publishes the id mapping for student-state migration, and leaves every axiom intact.

Splits are triggered at authoring time (too many points, or direct in-degree above the ceiling, since a topic needing many direct prerequisites is usually two topics), structurally (interleaving, which is mandatory), and empirically (halts concentrating at one point, or persistent failure among students whose prerequisites are all mastered).

### 1.4 Data model

```python
TopicId = str
CourseId = str

# Open parameters, fixed before authoring at scale:
Q_MIN         = ...   # minimum questions per knowledge-point pool
KP_MAX        = ...   # maximum knowledge points per topic
IN_DEGREE_MAX = ...   # soft ceiling on direct prerequisites per topic
D_MAX         = ...   # max due-review set size per selection pass

@dataclass(frozen=True)
class Question:
    id: str
    difficulty: float                 # calibrated scale

@dataclass(frozen=True)
class KnowledgePoint:
    worked_example: Content
    questions: list[Question]         # len >= Q_MIN
    key_prereqs: set[TopicId]         # ancestors of the owning topic;
                                      # nonempty unless it is a source

@dataclass(frozen=True)
class Topic:
    id: TopicId                       # retired, never reused, on split
    name: str                         # unique in the curriculum
    grade_band: str
    strand: str
    knowledge_points: list[KnowledgePoint]   # ordered easiest to hardest
    courses: set[CourseId]            # at least one

class KnowledgeGraph:
    topics: dict[TopicId, Topic]
    prereq: dict[TopicId, set[TopicId]]      # DIRECT prerequisites only:
                                             # the transitive reduction
    encompass: dict[TopicId, dict[TopicId, float]]
                                             # v -> {u: w}, w in (0, 1]
                                             # "v encompasses u"
```

No structure above contains per-student data. `validate(G)` is the executable checklist of the axioms.

## 2. Relations

### 2.1 Prerequisites

Definition (prerequisite edge). u -> v in P: u is a direct prerequisite of v. A student is not ready to learn v before mastering u, and the successors of a mastered topic are the candidate next steps.

Postulate (conjunctive readiness). ready(v, M) holds exactly when every direct prerequisite of v lies in M. There are no disjunctive prerequisites; where the mathematics offers genuine alternatives, re-granulate instead, by splitting or by introducing a common ancestor that captures the shared skill.

```python
def ready(t: TopicId, mastered: set[TopicId]) -> bool:
    return all(p in mastered for p in G.prereq[t])
```

Axiom (acyclicity). P is a DAG at all times; a mutation that would create a cycle is rejected at write time.

Axiom (reduced storage). P is stored as its transitive reduction; the closure P* is derived. A shortcut edge never changes readiness (with ancestrally closed mastery, its source is mastered whenever the longer path is) but inflates predecessor sets and distorts evidence propagation and covering computations.

Axiom (entry floor). The sources of P are exactly the curriculum's entry floor, currently 4th-grade material, and every other topic has at least one direct prerequisite.

Proposition (reachability). Every topic is reachable from the floor: walking backward can never revisit a node and can only stop at a source.

### 2.2 Encompassings

Definition (encompassing edge). `G.encompass[v][u] = w` with w in (0, 1] asserts that v encompasses u: one completed task on v is worth the fraction w of one full repetition on u. Weight 1 means fully encompassed; below 1, the component skill is exercised only in part. The map is indexed from the advanced topic downward because that is the direction credit flows.

Axiom (second DAG). W is acyclic, and every weight lies in (0, 1].

Postulate (independence). W is not derived from P. Most encompassed topics happen to be prerequisite ancestors, but neither containment is a rule: a topic can require familiarity with u without ever exercising it. Repetition credit flows only along W, never along bare prerequisite edges.

Postulate (static weights). A weight is a context-free property of an edge. Everything dynamic, such as discounting an implicit repetition that arrives before the encompassed topic's schedule calls for it, or scaling credit by accuracy, is student-model responsibility (its Fractional Implicit Repetition scheme). The graph answers "how much of u does one task on v inherently exercise", never "how much credit does this student receive right now".

Postulate (propagation). Credit propagates transitively, and the rule must satisfy: implied credit stays in (0, 1]; it is monotone non-increasing in edge weights; and through any single path it never exceeds the smallest weight on that path, so composition attenuates and never amplifies. The reference instantiation multiplies along a path and combines paths by maximum; the final choice is an open parameter fixed before authoring, since a weight's meaning depends on it.

```python
def implicit_credit(v: TopicId) -> dict[TopicId, float]:
    """Static credit implied by one completed task on v.
    Reference semantics: multiply along a path, max across paths."""
    credit: dict[TopicId, float] = {}
    def walk(x: TopicId, acc: float) -> None:
        for u, w in G.encompass[x].items():
            c = acc * w
            if c > credit.get(u, 0.0):
                credit[u] = c
                walk(u, c)
    walk(v, 1.0)
    return credit          # excludes v itself; callers add the explicit rep
```

### 2.3 Key prerequisites

Definition (key prerequisite). Per knowledge point, the prerequisite topics whose skills that point most directly exercises: the probable locus of failure when the point is failed. In the fine-graph picture these are the cross edges that survive the quotient, recorded with their source coarsened to the topic level. Their consumer is remediation.

Axiom (ancestry). Every key prerequisite of a point of t is an ancestor of t, not necessarily direct and often not: inside an exponents lesson, the point evaluating (-4)^3 links to Multiplying Negative Numbers several steps down, the skill actually failing when a student can write a power but cannot compute it. Every point of every non-source topic has at least one.

### 2.4 Courses

Definition (course, foundations). A course is a named subset of topics; courses may overlap, and every topic belongs to at least one. The foundations of course c are the topics outside c that are ancestors of topics in c; a course diagnostic assesses the course together with its foundations.

Axiom (derived views). Foundations are computed on demand, never authored, so they cannot disagree with the edge structure. The human-facing course graph, one node per course, is a derived summary whose aggregation must stay acyclic; any conflict is resolved by regenerating the summary.

```python
def foundations(course: Course) -> set[TopicId]:
    outside = lambda t: t not in course.topics
    return {a for t in course.topics for a in ancestors(t) if outside(a)}
```

### 2.5 Reachability queries

The closure P* is precomputed and maintained incrementally under edge mutations; at 10^4 topics a bitset closure occupies about 12.5 MB, so full materialization is cheap.

Axiom (query cost). Ancestor, descendant, and implicit-credit retrieval run in time linear in the size of the answer.

```python
def ancestors(t: TopicId) -> set[TopicId]: ...    # precomputed closure
def descendants(t: TopicId) -> set[TopicId]: ...
def successors(t: TopicId) -> set[TopicId]: ...   # direct out-neighbors
```

## 3. The student model

Definition (knowledge profile). Per student: repetitions accrued per topic, a review schedule per topic, a student-topic learning speed (a per-topic multiplier: 2x on an easy topic advances its schedule twice as fast, 0.5x half as fast), halt counts, and a mastery trichotomy per topic: mastered; conditionally completed, where evidence is only barely favorable and the topic is treated as mastered until dependent work falters; and not known.

Axiom (closure of mastery). The mastered set is ancestrally closed at all times.

```python
class StudentModel:
    """All dynamic state. The graph supplies structure and static
    weights; this model supplies everything time- and student-varying."""
    def install(self, student, profile): ...            # adopt a diagnostic result
    def add_reps(self, student, t, amount,
                 implicit=False): ...                   # accrue repetitions
    def due_reviews(self, student) -> dict[TopicId, float]: ...
                                                        # review demand d(t) > 0
    def reps_earned(self, task, results) -> float: ...  # speed x accuracy
    def discounted(self, credit, t, student) -> float: ...
                                                        # timing discount (FIRe)
    def evidence_weight(self, answer) -> float: ...     # slow correct: less
    def explicit_only(self, t, student) -> bool: ...    # hard topics absorb
                                                        # implicit credit poorly
    def quiz_difficulty(self, student) -> float: ...    # targets ~80% expected
```

Definition (frontier). For an ancestrally closed mastered set M, the frontier is the set of topics not in M whose direct prerequisites all lie in M: exactly the topics the student is ready to learn. New lessons are only ever served from the frontier.

```python
def frontier(mastered: set[TopicId]) -> set[TopicId]:
    return {t for t in G.topics
            if t not in mastered and ready(t, mastered)}

def on_mastered(state, t: TopicId) -> None:
    state.mastered.add(t)
    state.frontier.discard(t)
    for s in successors(t):                  # only the out-neighborhood
        if s not in state.mastered and ready(s, state.mastered):
            state.frontier.add(s)

def on_fell_back(state, t: TopicId) -> None:
    # a conditionally completed topic failed to hold up
    lost = ({t} | descendants(t)) & state.mastered
    state.mastered -= lost                   # preserves ancestral closure
    rebuild_frontier_locally(state, lost)
```

Axiom (locality). Frontier maintenance is incremental: per-update cost is proportional to the affected neighborhood, never to the size of the graph.

## 4. Algorithms

The session loop is the map; the subsections fill in each call.

```python
def next_session(student, course, minutes: float) -> None:
    if not student.diagnosed:
        run_diagnostic(student, course)
        return
    for task in select_tasks(student, minutes):
        results = present(task)
        on_task_completed(student, task, results)    # includes remediation
    if quiz_due(student):
        present(assemble_quiz(student, course))
```

### 4.1 Diagnostic

A course plus its foundations spans 500 to 1,000 topics, far too many to probe one by one. Offline, per course, the scope is compressed into an assessment pool: the fewest topics that still cover the whole scope under a declared coverage relation. This is minimum set cover, NP-hard in general; greedy carries the 1 + ln n guarantee, exact solving is viable on instances this small, and the binding requirement is full coverage within the per-course question budget, roughly an order of magnitude below the raw scope.

```python
def diagnostic_pool(course: Course) -> set[TopicId]:
    scope = course.topics | foundations(course)
    pool: set[TopicId] = set()
    uncovered = set(scope)
    while uncovered:
        t = max(scope, key=lambda t: len(covers(t) & uncovered))
        pool.add(t)
        uncovered -= covers(t)              # covers(): open parameter
    return pool
```

Online, the exam repeatedly asks the question expected to reveal the most about the student's frontier. A correct answer is positive evidence about the topic and its ancestors; an incorrect answer is negative evidence about the topic and its descendants; evidence attenuates with distance and is weighed by answer quality.

```python
def run_diagnostic(student, course) -> None:
    pool = diagnostic_pool(course)
    ev: dict[TopicId, float] = defaultdict(float)    # signed evidence
    asked = 0
    while asked < BUDGET and not confident(ev, course):
        t = max(pool, key=lambda t: expected_information(t, ev))
        answer = ask(student, pick_question(t))
        w = student_model.evidence_weight(answer)
        if answer.correct:
            for u in {t} | ancestors(t):
                ev[u] += w * attenuation(u, t)
        else:
            for u in {t} | descendants(t):
                ev[u] -= w * attenuation(u, t)
        asked += 1
    student_model.install(student, infer_profile(ev))
```

`infer_profile` thresholds the evidence into the mastery trichotomy and must yield an ancestrally closed mastered set; project it if raw thresholding is not. Weighting, attenuation, the stopping rule, and thresholds are diagnostic policy; the graph's obligations are fast closures and stable ids.

### 4.2 Task selection

Selection teaches from the frontier, unlocking successors the moment mastery lands, and satisfies due reviews at the same time. Repetition compression is what keeps review from crowding out learning: credit flows down encompassings, so a well-chosen task knocks out several due reviews at once, and a frontier lesson that does so beats the reviews it replaces. Formally this is weighted set multicover, NP-hard in general; the greedy pass carries the standard logarithmic guarantee and must run at interactive latency for due sets up to D_MAX.

```python
def select_tasks(student, minutes: float) -> list[Task]:
    due = student_model.due_reviews(student)
    chosen: list[Task] = []
    while due and total_time(chosen) < minutes:
        cands = ([Lesson(t) for t in student.frontier] +
                 [Review(t) for t in due])
        best = max(cands, key=lambda c: value_rate(c, due, student))
        chosen.append(best)
        absorb_projected_credit(best, due, student)   # shrink demands
    while total_time(chosen) < minutes and student.frontier:
        chosen.append(pick_low_interference_lesson(student, chosen))
    return chosen

def value_rate(task: Task, due, student) -> float:
    """Learning per unit time. Implicit knockouts count, except for
    topics pinned to explicit review."""
    covered = sum(min(due[u], student_model.discounted(c, u, student))
                  for u, c in implicit_credit(task.topic).items()
                  if u in due and not student_model.explicit_only(u, student))
    own = due.get(task.topic, 0.0) + new_learning_value(task)
    return (own + covered) / est_time(task, student)
```

`pick_low_interference_lesson` fills leftover time with frontier lessons that are mutually unrelated under the relatedness measure: closely related material taught back to back causes associative interference, so related topics are spaced apart and dissimilar ones interleaved.

### 4.3 Completing a task

A completed task earns an explicit repetition on its topic and implicit, discounted repetitions on everything the topic encompasses. Speeds and discounts are student-model policy; the graph contributes the static credit map.

```python
def on_task_completed(student, task: Task, results) -> None:
    reps = student_model.reps_earned(task, results)
    student_model.add_reps(student, task.topic, reps)
    for u, c in implicit_credit(task.topic).items():
        student_model.add_reps(
            student, u,
            student_model.discounted(reps * c, u, student),
            implicit=True)
    if isinstance(task, Lesson) and results.passed:
        on_mastered(student.state, task.topic)       # unlock successors
    if isinstance(task, Lesson) and results.halted:
        on_lesson_halted(student, task.topic_obj, results.kp_index)
```

### 4.4 Remediation

Too many misses halts a lesson; the student does unrelated work and re-attempts later. A second halt at the same knowledge point serves remedial review on that point's key prerequisites, which reach the true point of struggle even several steps down the hierarchy.

```python
def on_lesson_halted(student, topic: Topic, kp_index: int) -> None:
    student.halts[(topic.id, kp_index)] += 1
    if student.halts[(topic.id, kp_index)] >= 2:
        for p in topic.knowledge_points[kp_index].key_prereqs:
            schedule_remedial_review(student, p)
```

Halts concentrating at one point across many students are also a structural signal, feeding the split triggers.

### 4.5 Quizzes

Quizzes audit knowledge believed solid: topics sampled from the mastered set with low mutual relatedness, prioritizing the enrolled course, deprioritizing topics quizzed recently or exercised implicitly through other quizzable topics. Difficulty targets an expected score near 80 percent. A missed item immediately schedules a remedial review on its topic.

```python
def assemble_quiz(student, course, n: int = N_QUIZ) -> list[Question]:
    def weight(t: TopicId) -> float:
        w = 1.0
        if t in course.topics:                w *= COURSE_BOOST
        if recently_quizzed(student, t):      w *= REPEAT_PENALTY
        if encompassed_by_quizzable(t, student.mastered):
                                              w *= ENCOMPASS_PENALTY
        return w
    picks = diverse_sample(student.mastered, weight, n,
                           spread=relatedness)
    target = student_model.quiz_difficulty(student)
    return [question_near(t, target) for t in picks]
```

## 5. Construction

Decompose the topics based on content. Pay attention to atomicity one skill or idea, one sitting and ground them in content excercise problems. It will start with a curated set of topics and then expand based on student needs.

Postulate (traceability). Every edge in P and W traces to a recorded solution-step analysis; no edge rests on intuition about topic similarity.

Step analysis. For each topic, enumerate the solution steps of representative problems, including the hardest variation of every knowledge point, and classify each step. Introduced by this topic: contributes nothing. Exercises a skill taught elsewhere: a prerequisite edge plus an encompassing edge weighted by the extent of exercise. Assumes familiarity without exercising it: a prerequisite edge only.

```python
for kp in topic.knowledge_points:
    for step in solution_steps(hardest_variant(kp)):
        if taught_in(step, topic):
            continue
        src = topic_teaching(step)
        add_prereq(topic, src)
        if exercised(step):
            add_encompass(topic, src, weight=extent_of_exercise(step))
# afterwards: prereq := transitive_reduction(prereq)
```

The worked multiplication 39 x 6 illustrates the method: its steps are one-digit multiplication and adding a one-digit number to a two-digit number, so Multiplying Two-Digit Numbers by One-Digit Numbers encompasses both. Splits consult the same analysis when reassigning edges.

Weights and key prerequisites come from the same analysis: full weight when the general form of the encompassed skill is exercised, fractional for special cases or fragments, and key prerequisites per point as the ancestor skills most directly exercised there. The propagation rule is fixed first, since a weight's meaning depends on it.

Review and refinement. Every topic, with its incident edges, weights, and key prerequisites, is reviewed by a second expert before entering the graph, disagreements adjudicated rather than averaged. After launch, learner data is monitored against the graph's predictions: persistent failure on v among students with all direct prerequisites mastered points to a missing edge or a granularity fault, corrected by a split; remedial review that fails to lift re-attempt pass rates points to mistargeted key prerequisites; depressed review performance on topics maintained mostly through implicit credit points to inflated weights. Each monitor has a trigger threshold and routes back to re-authoring.

## 7. Validation

Machine checks gate every mutation, splits included; all run in polynomial time.

```python
def validate(G: KnowledgeGraph) -> None:
    assert is_acyclic(G.prereq)                        # axiom: acyclicity
    assert is_acyclic(G.encompass)                     # axiom: second DAG
    assert G.prereq == transitive_reduction(G.prereq)  # axiom: reduced storage

    sources = {t for t in G.topics if not G.prereq[t]}
    assert sources == ENTRY_FLOOR                      # axiom: entry floor

    for t in G.topics.values():
        assert t.courses
        assert 1 <= len(t.knowledge_points) <= KP_MAX
        assert len(G.prereq[t.id]) <= IN_DEGREE_MAX    # soft; warn, review
        assert difficulty_nondecreasing(t.knowledge_points)
        for kp in t.knowledge_points:
            assert len(kp.questions) >= Q_MIN          # axiom: pool size
            if G.prereq[t.id]:                         # non-source topic
                assert kp.key_prereqs
            assert kp.key_prereqs <= ancestors(t.id)   # axiom: ancestry

    for v, out in G.encompass.items():
        for u, w in out.items():
            assert 0.0 < w <= 1.0
```

Axiom (completeness, semantic). Every skill invoked by any worked example or question is either introduced by its own topic or taught by an ancestor. This is what makes gating sound: a ready student possesses everything the lesson draws on. It is not mechanically decidable and is enforced by audit: sample questions, have an expert who did not author the topic list the skills each invokes, verify containment in the topic or its ancestry, and accept only below a declared violation rate.

Two simulations complete validation. Synthetic students with known profiles must be recovered by the diagnostic within a declared frontier-error tolerance and question budget. And each course must admit a gating-respecting schedule from its entry state to completion; acyclicity plus completeness imply one exists, so this is a regression check, not new mathematics.

## 8. Scale

The model targets 10^3 to low 10^4 topics, sparse in both relations, with direct in-degrees typically one to three. Whole-graph computations (closure, reduction, covering) run offline in polynomial time; the materialized closure fits in roughly 12.5 MB of bitsets at 10^4 topics. Online work is bounded by output-linear closure retrieval, neighborhood-local frontier maintenance, constant-time key-prerequisite lookup, and a greedy multicover loop bounded by D_MAX, and implementations must preserve those bounds as the graph grows.

## 9. Open parameters

Fixed, recorded, and versioned before authoring at scale, since the meaning of authored content depends on them and late changes force review of affected content:

- The encompassing propagation rule (reference: multiply along a path, max across paths), subject to the propagation postulate.
- The difficulty scale and calibration procedure for questions.
- The relatedness measure for interference-aware interleaving and quiz diversity; candidates: shared-ancestor overlap, graph distance, strand metadata.
- The coverage relation `covers(t)` and the per-course diagnostic question budget.
- The constants Q_MIN, KP_MAX, IN_DEGREE_MAX, D_MAX, and the quiz size N_QUIZ.
- The units and computation of review demand d(t), together with the timing-discount policy.
- The student-state migration policy consuming the id mapping published by splits.
- The trigger thresholds for the empirical monitors.