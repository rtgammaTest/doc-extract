# Gold data

`sample_agreement.csv` scores the synthetic fixture in `../fixtures/`. It exists
so the repo runs end to end on a clean clone, with no credentials and no cost.

The real gold set is the hand-verified CSVs from
[`covenant-definition-graph`](https://github.com/rtgammaTest/covenant-definition-graph):
two credit agreements, 38 covenants, 46 of 46 assertions verified by hand. That
is the expensive part and it is already done.

Neither repo redistributes the agreements themselves. Fetch them from EDGAR with
that project's `fetch.py`, which requires `SEC_USER_AGENT` to be set.

## Format

    doc_id,field_path,value,citation_section

One row per field. `doc_id` must match what the extractor used -- the file stem
for a local run, the full `gs://` path for one triggered by Eventarc.

Ratio values are compared after normalisation, so `4.00:1.00` and `4.00 to 1.00`
score the same. Everything else is compared as written, with whitespace
collapsed and case ignored.
