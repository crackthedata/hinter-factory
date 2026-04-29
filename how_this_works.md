# How Hinter Factory works

A plain-English guide to the core data model, the relationship between **tags**, **labeling functions** ("hinters"), and **gold labels**, and the full label → run → evaluate → fix loop.

If you want code-level detail, the canonical references are `services/ml/app/models.py` (schema), `services/ml/app/lf_executor.py` (LF runtime), and `services/ml/app/evaluation.py` (aggregation + metrics). This document is the conceptual overview.

---

## 1. The big picture

Hinter Factory is a **weak supervision** workbench. Instead of hand-labeling thousands of documents, you write many cheap, noisy heuristics ("hinters" / "labeling functions"), run them across your corpus, and combine their votes into a prediction. You then gold-label a small validation set by hand and use the **Evaluation** page to see which of your hinters are wrong, so you can fix them and iterate.

```
Documents ──► Labeling Functions vote ──► Aggregated prediction
                                              │
Gold Labels ◄────────── compare ──────────────┘
        │
        └──► Evaluation: precision / recall / per-document errors
```

---

## 2. The entity hierarchy

```
Project
 ├── Document         (a row in your CSV/JSON: text + metadata)
 ├── Tag              (one yes/no concept, e.g. "is_invoice")
 │     ├── LabelingFunction   (one heuristic that votes on this tag)
 │     ├── LabelingFunction
 │     └── LabelingFunction
 ├── GoldLabel        (your manual truth: (document, tag) → +1/0/-1)
 ├── LfRun            (one batch execution of LFs for a tag)
 │     ├── LfRunLabelingFunction   (which LFs were in this run)
 │     └── LfRunVote              (one row per (document, LF) that fired)
 └── ProbabilisticLabel (aggregated per (document, tag); future label model)
```

A **project** is a fully self-contained workspace. Switching projects in the header swaps in a different set of documents, tags, LFs, gold labels, and runs. Tag names are unique *per project* — the same name can exist in different projects.

---

## 3. Tags vs. Labeling Functions

This is the relationship most people want clarified, so it gets its own section.

### A `Tag` is a concept
A tag is a single binary classification target. Examples:

- `is_invoice`
- `is_complaint`
- `mentions_pricing`
- `requires_legal_review`

That's it. A tag has a name and lives in a project. It does not contain rules, patterns, or logic — it's just the *thing you want to know about each document*.

### A `LabelingFunction` is a heuristic that votes on **one** tag
A labeling function (LF) is a concrete rule that, when applied to a document, returns one of:

- **`+1`** — "yes, this document has the tag"
- **`0`** — abstain / not sure / doesn't apply
- **`−1`** — "no, this document does not have the tag" *(supported by the schema; the built-in executors currently only emit `+1` or `0`)*

Each LF has a `tag_id` foreign key. **An LF belongs to exactly one tag.** You cannot have one LF that simultaneously votes on `is_invoice` and `is_complaint` — you'd create one LF per tag.

There are five LF `type`s, configured via a JSON `config` blob:

| Type | What it does | Config keys |
|---|---|---|
| `regex` | Fires `+1` if a regex matches the text | `pattern`, optional `flags` (e.g. `"i"` for case-insensitive) |
| `keywords` | Fires `+1` if **any** (or **all**) of a list of keywords appears | `keywords: string[]`, `mode: "any" \| "all"` |
| `structural` | Fires `+1` if the document satisfies length / caps-ratio / punctuation-ratio bounds | `length_gte`, `length_lte`, `caps_ratio_gte`, `caps_ratio_lte`, `punctuation_ratio_gte`, `punctuation_ratio_lte` |
| `zeroshot` | Reserved for a zero-shot classifier (currently stub: always `0`) | — |
| `llm_prompt` | Reserved for an LLM prompt-based classifier (currently stub: always `0`) | — |

**Cardinality:** *Tag 1 — N LabelingFunction.* One tag can have many LFs (a regex *and* a keyword list *and* a structural rule, all voting on the same `is_invoice` tag). Each LF only ever votes on its own tag.

### Why you don't manually vote at the LF level
A common question: *"I can vote +1/0/−1 per document for a tag — do I also vote per LF?"* No, and that's intentional.

| Voter | Votes on | How the vote is produced | Stored in |
|---|---|---|---|
| **You (human)** | `(document, tag)` | Manual click in Explore | `GoldLabel` |
| **A labeling function** | `(document, tag)` | **Computed** by the LF code (regex match, keyword hit, etc.) | `LfRunVote` |

An LF *is* the vote. A regex LF deterministically returns `+1` or `0` for any given document — there is nothing for a human to click. If the regex is wrong on a specific document, the fix is to **edit the regex** (or add another LF that handles the case), not to override that one vote. That's the whole point of having a *function*: it generalizes.

You **can** see what each individual LF voted, in two places:

1. **LF Studio → Preview** — runs one LF against recent documents so you can sanity-check its output before running a batch.
2. **Evaluation → Per-LF votes drill-down** — on every error row, expand *Per-LF votes* to see exactly what each LF said about that document. This is the diagnostic surface that tells you which LF to edit.

---

## 4. The LF run

A run is **a batch execution of a chosen subset of LFs against the entire corpus, scoped to one tag**.

When you click *Run* in LF Studio:

1. An `LfRun` row is created with `status="pending"` and `tag_id` set.
2. An `LfRunLabelingFunction` row is created for each chosen LF, recording its column position so the result can be reconstructed as a sparse matrix.
3. Every selected LF is executed against every document in the project. Whenever an LF emits a non-zero vote, an `LfRunVote(run_id, document_id, labeling_function_id, vote)` row is written. **Abstains (`0`) are not stored** — the matrix is sparse.
4. The run flips to `status="completed"` with `documents_scanned` and `votes_written` populated.

You can have many runs per tag — typically one per iteration of your LF authoring loop. The Evaluation page defaults to the latest *completed* run.

The sparse matrix is exposed at `GET /v1/lf-runs/{id}/matrix` for downstream tools.

---

## 5. Gold labels and the validation set

`GoldLabel` is your manual ground truth, keyed on `(document_id, tag_id)`:

- `+1` — yes, this document has the tag
- `0` — you genuinely can't tell / not applicable (excluded from precision and recall)
- `−1` — no, this document does not have the tag

You create gold labels in **Explore**: pick a tag in the bottom of the filter bar, then click `+1 / 0 / −1` next to documents you're confident about. There's no fixed "validation set" object — the validation set for a tag is simply **all gold labels for that tag** with value `+1` or `−1`.

A few dozen confident gold labels per tag is usually enough to start — you can always add more as you discover ambiguous cases during evaluation.

---

## 6. Evaluation: aggregating LF votes vs. gold

For each gold-labeled document in the validation set, the evaluator:

1. **Sums** the LF votes for that document from the chosen run.
2. **Predicts** `+1` if sum > 0, `−1` if sum < 0, abstains (`0`) if sum == 0. *(This is `aggregate_vote` in `services/ml/app/evaluation.py`. It is a pure function — easy to swap for a probabilistic label model later.)*
3. **Categorizes** the outcome into one of seven buckets:

| Category | Gold | Predicted | Meaning |
|---|---|---|---|
| `true_positive` | +1 | +1 | LFs correctly identified a positive |
| `true_negative` | −1 | −1 | LFs correctly rejected a negative |
| `false_positive` | −1 | +1 | LFs wrongly fired on a negative — **fix the offending LF** |
| `false_negative` | +1 | −1 | LFs voted negative on a true positive — **rare; means an LF is actively wrong** |
| `abstain_on_positive` | +1 | 0 | No LF fired — counts as a missed recall in production |
| `abstain_on_negative` | −1 | 0 | No LF fired — fine for precision but lowers coverage |
| `gold_abstain` | 0 | any | You said you weren't sure; excluded from metrics |

### Metrics

```
precision = TP / (TP + FP)
recall    = TP / (TP + FN + abstain_on_positive)   # missed positives include abstains
f1        = 2·P·R / (P + R)
coverage  = (TP + TN + FP + FN) / considered       # fraction where LFs took a stance
```

Each metric is `null` when its denominator is zero, so you don't get spurious 0s on a fresh tag.

The Evaluation page sorts errors first (FP, FN, abstain-on-positive), then everything else, so the most actionable rows are at the top. Each row has a *Per-LF votes* dropdown showing which specific LF said what — that's how you find the LF to edit.

---

## 7. The end-to-end loop

```
1. Create a project at /projects (required — every page is scoped to one).

2. Explore → ingest a CSV/JSON corpus.
        Pick the file → set Text column → Upload.

3. LF Studio → create a tag (e.g. "is_invoice").

4. LF Studio → author one or more LFs for the tag.
        Use Preview to sanity-check on sample documents.

5. Explore → gold-label a small validation set.
        Pick the tag in the filter bar, vote +1/-1 on ~20-50 confident docs.

6. LF Studio → click Run to execute all selected LFs across the corpus.

7. Evaluation → pick the tag.
        Read precision, recall, F1, coverage.
        Open false-positive / false-negative rows.
        Expand Per-LF votes to see which LF caused each mistake.

8. Back to LF Studio → tighten the offending regex,
        narrow the keyword list, add a structural guard,
        or write a new LF for cases nothing covered.

9. Re-run, re-evaluate. Repeat until metrics are good enough.

10. (Optional) /projects → Export to ship the workspace
        to another instance of Hinter Factory.
```

---

## 8. Quick reference: the schema, in one paragraph

A `Project` owns `Document`s, `Tag`s, `LabelingFunction`s, `GoldLabel`s, `LfRun`s, and `ProbabilisticLabel`s. A `Tag` is a single binary concept. A `LabelingFunction` belongs to exactly one tag and is a heuristic that emits `+1 / 0 / −1` per document. A `GoldLabel` is your manual `+1 / 0 / −1` per `(document, tag)`. An `LfRun` is a batch execution of a chosen set of LFs for a tag, producing one `LfRunVote` per `(document, LF)` that fired (abstains are not stored). The Evaluation endpoint sums the LF votes per document, compares the sum-majority prediction to the gold label, and produces per-bucket counts plus precision / recall / F1 / coverage.

---

## 9. FAQ

**Is "hinter" the same as "labeling function"?**
Yes. "Hinter" is the product/UX name; "labeling function" (or "LF") is the technical term. Each one is a heuristic that *hints* at whether a document matches a tag. There is no separate `Hinter` entity in the database.

**Can one LF vote on multiple tags?**
No. Each LF is bound to a single tag via `tag_id`. If you want the same regex to score two tags, create two LFs (one per tag).

**Can I override an LF's vote on a single document?**
No, and the design deliberately discourages it — per-document overrides defeat the purpose of having a function. The intended workflow is: spot the bad LF in the Evaluation per-LF breakdown → edit the LF in Studio → re-run.

**What does `0` mean for a gold label vs. an LF vote?**
Same semantics in both places: *abstain / unsure / not applicable*. For LFs it usually means "the rule didn't fire". For gold labels it means "I genuinely can't tell" — those rows are excluded from precision and recall but still surfaced as `gold_abstain`.

**Why isn't there a probabilistic label model?**
There's a `ProbabilisticLabel` table reserved for one, but Evaluation currently uses a simple sum-of-votes majority (`aggregate_vote`) so the system is debuggable end-to-end without an opaque model. Swapping in a Snorkel-style label model is a future enhancement; the aggregator is a pure function for exactly this reason.

**Why don't `zeroshot` and `llm_prompt` LFs fire?**
They're scaffolded in the schema and UI but the executors are stubs that return `0`. Wiring them up to an actual model is a planned extension.

---

For storage and migration details, the export/import format, and the API surface, see the project [`README.md`](./README.md).
