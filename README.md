# doc-extract — a versioned document extraction service

A document lands in object storage. A model extracts it against a schema. Deterministic
rules decide whether the output is acceptable. Every extracted field carries its citation
and the version of the extractor that produced it, so extraction can be re-run and improved
without rewriting what the system believed last quarter.

An eval scored against hand-verified ground truth runs on every prompt or model change, and
a regression fails the build.

```bash
git clone <this repo> && cd doc-extract
pip install jsonschema      # the only dependency the core needs
make demo
```

That extracts a sample agreement, runs it through the gate, scores it against gold data and
writes a report — with no GCP project, no API key and no network. The model is a regex
baseline by default; switching to Gemini is two environment variables.

---

## Why this shape

Three claims, and the architecture exists to make each one true:

1. **Extraction is non-deterministic, so acceptance must not be.** The model proposes;
   a JSON schema and a set of invariants dispose. Nothing reaches the store unvalidated.
2. **Derived data is versioned.** Re-extracting a document with a better model writes a new
   version against the same immutable source. The old answer stays queryable. This is the
   same requirement GIPS 2.A.21 imposes on estimated valuations, applied to AI output.
3. **Every field is traceable.** Citation (section, page, character offset) plus
   `extractor_version` on each field, or an auditor has nothing to check.

---

## Architecture

```
  GCS: raw/            (immutable — the system of record)
    │
    │  Eventarc: google.cloud.storage.object.v1.finalized
    ▼
  Cloud Run: extractor
    │  1. fetch document                      main.py
    │  2. segment: clauses + referenced defs  segment.py
    │  3. Vertex AI → structured output       model.py
    │  4a. validate the response shape        schema.py
    │  4b. validate against the document      invariants.py
    │  5. reject → dead-letter;  accept → write
    ▼
  BigQuery: extractions
     doc_id, field_path, value, citation, confidence,
     extractor_version, model, prompt_sha, extracted_at
```

Nothing is updated in place. A re-run inserts rows with a new `extractor_version`.
`current` is a view selecting the latest version per `(doc_id, field_path)`.

The same pipeline maps onto AWS with no architectural change: S3 → EventBridge → Step
Functions over Lambda → Bedrock → the same gate → Parquet in S3 with a Glue catalog. Only
the plumbing is cloud-specific. Steps 4a and 4b are ordinary application code and run
unchanged either way.

### Layout

```
extractor/
  schema.py        single source of truth — the JSON schema AND the DDL
  segment.py       clause selection + transitive definition closure
  invariants.py    the gate: deterministic acceptance rules
  model.py         Vertex AI, and a regex baseline that needs neither
  store.py         append-only BigQuery or append-only JSONL
  pipeline.py      fetch → segment → extract → validate → write
  main.py          FastAPI shim for the Eventarc push
  cli.py           the same pipeline over local files
  prompts/v1.txt   versioned; its sha256 lands on every row
sql/
  generate.py      writes the DDL from schema.py
  00*.sql          generated, committed, diffable
eval/
  fixtures/        a synthetic agreement so the repo runs on a clean clone
  gold/            hand-verified ground truth
  score.py         field accuracy, citation validity, failure taxonomy
  gate.py          fails the build on a regression
  reports/         every run, committed
tests/             65 tests, no network, no credentials
Dockerfile         at the root, because that is where `gcloud run deploy --source .` looks
```

---

## Two schemas that must not drift

There are two descriptions of a row in this system: the JSON schema the model is held to,
and the SQL table the rows land in. Nothing stops those drifting apart, and when they do
you get extractions that pass validation and then fail to write, or columns nobody ever
fills. Both failures surface late and look like something else.

So both are generated from one registry in `extractor/schema.py`. `make sql` writes the
DDL, the generated files are committed so a schema change shows up as a reviewable SQL
diff, and `tests/test_schema_ddl_drift.py` fails the build when they stop matching.

The store is append-only, so schema changes must be **additive**. Adding a column is fine —
old rows carry null, and `extractor_version` says why. Changing a column's type is not,
because it silently reinterprets history, which is exactly the property the versioning
exists to prevent.

---

## The gate

These are not confidence thresholds. A confidence threshold asks the model how sure it is,
which is asking the thing under test to grade itself. Every rule in `invariants.py` is a
fact that can be checked against the document or against arithmetic:

| Invariant | What it catches |
|---|---|
| `citation_resolves` | the cited section does not occur in the source — a confident hallucination |
| `ratio_threshold_format` | `4.00:1.00` and `4.0 to 1.0` reaching the store as different answers |
| `direction_in_enum` | `"maximum"` where the schema means `max` |
| `maintenance_has_frequency` | a maintenance covenant with nothing that says how often it is tested |
| `step_down_dates_ordered` | two thresholds in force at once, which makes the covenant untestable |
| `defined_term_resolves` | a term that is not defined in *this* agreement |
| `field_path_well_formed` | anything that would not join to the rest of the portfolio |

A record failing any rule is written to the dead-letter table with the rule's name and the
offending value — not dropped, because the failures are the dataset the next extractor
version is improved against.

`maintenance_has_frequency` is covenant-scoped: it rejects every field of that covenant
rather than just the offending one, because a maintenance covenant missing its frequency is
not three good fields and one bad one. The covenant as a whole cannot be evaluated.

---

## The eval

`eval/score.py` compares the current view against the gold CSVs and emits:

- **field accuracy** — exact match after normalisation, so `4.00:1.00` and `4.00 to 1.00`
  score the same. Measuring formatting is not measuring extraction.
- **citation validity** — does the cited section exist and contain the value?
- **failure taxonomy** — `match`, `wrong_value`, `bad_citation`, `missing_field`,
  `hallucinated_field`, `unresolved_defined_term`

That last category is the hypothesis under test. `covenant-definition-graph` predicted that
extraction fails on definition closure rather than on numbers. This measures whether that
is true.

### What the eval caught first

The first scored run against a real model came back at 91.7% with three misses — one
`wrong_value` and two `hallucinated_field`, all of them on `defined_term`. Reading them,
all three were the model being right and the hand-verified gold being wrong: two covenants
whose defined term the gold set simply omitted, and one where the gold named a term that
does not appear in the cited section at all.

The gold data was the defective artifact, and the citation column is what proved it —
every extraction pointed at a section you could go read and check. The number alone would
have sent someone tuning a prompt that was already correct.

This is the argument for the whole apparatus in one incident: an eval is only worth having
if you read the failures, and citations are what let you tell "the model is wrong" apart
from "the answer key is wrong".

Read the taxonomy, not the headline. A run that trades `missing_field` for `wrong_value`
has got **worse** even if accuracy went up, because a wrong number with a citation is
harder to catch than a gap.

Every report is committed. The trend across extractor versions is the only thing that says
whether the work is converging.

### The baseline is a regex

`model.py`'s `FakeModel` is a careful regex extractor over the same segmented text, not a
mock that returns a canned blob. That makes it the number the real model has to beat:

> "An LLM extracts covenants" is not interesting. "An LLM beats a careful regex by N points,
> and here is where each of them fails" is.

It also means the tests, the eval and CI all run with no credentials and no cost.

---

## Running it

```bash
make help        # every target
make test        # 55 tests; pytest if installed, stdlib runner otherwise
make demo        # extract the fixture, score it, write a report
make sql         # regenerate the DDL after changing the registry
make eval        # score whatever is in out/
make gate        # fail if the eval regressed
```

No pytest? `python3 run_tests.py` runs the suite with nothing but the standard library.

### Against real infrastructure

```bash
export PROJECT=your-project REGION=us-east4
make bootstrap   # bucket, dataset, tables, service account, IAM
make deploy      # Cloud Run + the Eventarc trigger

gcloud storage cp agreement.txt gs://${PROJECT}-docs-raw/raw/
```

Then set `MODEL_BACKEND=vertex` and `STORE_BACKEND=bigquery`. See `.env.example`.

### Three model backends

| `MODEL_BACKEND` | What it calls | What it needs |
|---|---|---|
| `fake` | the regex baseline in `model.py` | nothing |
| `vertex` | Gemini on Vertex AI | a service account with `roles/aiplatform.user`, and publisher-model access |
| `gemini-api` | Gemini on `generativelanguage.googleapis.com` | `GEMINI_API_KEY` from [AI Studio](https://aistudio.google.com/apikey) |

`gemini-api` exists because Vertex publisher-model access is a separate entitlement from
the Vertex API itself. A project can have `aiplatform.googleapis.com` enabled, billing
linked, and a service account holding `roles/aiplatform.user`, and still get
`404 Publisher model ... was not found` for every model name. The API-key path does not
go through project IAM at all, so it works where that one does not.

**`VERTEX_LOCATION` is not `REGION`.** Gemini is served from a subset of regions, and
`us-east4` is not one of them -- calling it there returns `404 Publisher model ... was not
found`, which reads like a bad model name rather than a bad location. Cloud Run, GCS and
BigQuery stay wherever you put them; `VERTEX_LOCATION` defaults to `us-central1`.

### Least privilege

The service account gets `aiplatform.user`, `bigquery.dataEditor` and
`storage.objectViewer`. Note what is absent: no `storage.objectAdmin`, no
`storage.objectCreator`. **The service can never modify the source it reads from** — which
is the property that makes a re-run safe to repeat, and the same principle as making a
backup bucket read-only to the job that reads it.

---

## Cost

The only meaningful line item is inference. Everything else sits inside free tiers at this
volume, and the default backends cost nothing at all.

**Token estimate per eval run** (both credit agreements, covenant sections plus referenced
definitions rather than whole documents): ~80K input, ~6K output.

| Model | Input / 1M | Output / 1M | Per eval run |
|---|---|---|---|
| Gemini 2.5 Flash-Lite | $0.10 | $0.40 | ~$0.01 |
| Gemini 2.5 Flash | $0.30 | $2.50 | ~$0.04 |
| Gemini 3.5 Flash-Lite | $0.30 | $2.50 | ~$0.04 |
| Gemini 3.1 Pro Preview | $2.00 | $12.00 | ~$0.23 |

**A weekend of iteration — call it 200 eval runs:**

- Developing on Flash: **~$8**
- Final comparison runs on Pro: **~$10–20**
- **Total: $10–30**

Batch mode is roughly half price if you queue eval runs rather than calling interactively.

| Service | Cost |
|---|---|
| Cloud Run | $0 — scales to zero; free tier is 2M requests/month |
| GCS | ~$0 — a few MB |
| BigQuery | $0 — free tier is 10 GB storage, 1 TB queries/month |
| Eventarc | ~$0 at this volume |
| Artifact Registry | $0 — 0.5 GB free, the image is ~200 MB |
| Cloud Build | $0 — 120 free build-minutes/day |

**Set a budget alert at $50 before you start.** The failure mode that costs money is a retry
loop sending whole documents; the alert catches it, and `MAX_RETRIES=2` prevents it. The
retry in `model.py` only fires on unparseable output — quota, auth and 5xx are not made
better by sending the document again.

Prices verified October 2026 against Google's published Vertex AI rates, for the model
generation named in the table. **Model availability moves faster than the prices do:**
`gemini-2.5-flash` is already retired for new accounts, and both the Vertex and AI Studio
APIs report that as a bare `404 ... was not found or your project does not have access`,
with no mention of deprecation. Before concluding you have a permissions or billing
problem, list what your credentials can actually call:

```bash
curl -H "x-goog-api-key: $GEMINI_API_KEY" \
  https://generativelanguage.googleapis.com/v1beta/models
```

The default `MODEL_ID` is `gemini-3.8-flash`; re-price the table for whichever model you
pin.

---

## If you run short of time

Cut in this order. The first two are the project; everything else is plumbing.

1. **Keep** — object storage → compute → model → schema + invariants → store, with citation
   and `extractor_version`
2. **Keep** — the eval against gold data, even run by hand
3. Cut the GitHub Actions gate; run `score.py` manually and commit the reports
4. Cut Eventarc; use `python -m extractor.cli` with the object path
5. Cut BigQuery; `STORE_BACKEND=jsonl` writes the same columns to files, and the eval reads
   either
6. Cut everything resembling a UI

A version with items 1 and 2 only is still the whole story.

---

## What this demonstrates

- Production AI deployment inside your own cloud tenancy — the data never leaves
- Structured output against a schema, with deterministic acceptance
- Versioned derived data: re-extract history without destroying it
- Per-field citation and lineage
- Evaluation against ground truth, with a failure taxonomy
- Regression gating on a non-deterministic component
- Least privilege: the service cannot write to the source it reads
- One registry generating both the model contract and the table, with a test that fails on
  drift

The two-minute version:

> "Documents land in object storage, Cloud Run calls Vertex with a schema, and the output is
> validated against deterministic invariants before anything is persisted. Every field
> carries its citation and the extractor version that produced it, so I can re-run extraction
> as models improve and still reproduce what the system believed last quarter. The eval runs
> against a hand-verified gold set on every prompt change — a regression fails the build.
> Accuracy is X%, against Y% for a regex baseline, and the failures cluster on defined-term
> references."

Fill in X and Y before you say it. `make demo` gives you Y.

---

## Licence

MIT. See [LICENSE](LICENSE).

The credit agreements this was built against are not redistributed here. Fetch them from
EDGAR with `covenant-definition-graph`'s `fetch.py`, which requires `SEC_USER_AGENT`.
