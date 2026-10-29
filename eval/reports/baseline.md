# Eval report - regex baseline

_2026-10-29 22:56 UTC_

**Accuracy: 71.4%** over 14 gold fields  
**Citation validity: 100.0%**

## Failure taxonomy

| Outcome | Count | Share | Meaning |
|---|---:|---:|---|
| `match` | 10 | 71.4% | value and citation both correct |
| `wrong_value` | 0 | 0.0% | cited the right section, read the wrong number |
| `bad_citation` | 0 | 0.0% | right answer, wrong or absent provenance |
| `missing_field` | 4 | 28.6% | gold field never extracted |
| `hallucinated_field` | 0 | 0.0% | extracted a field with no gold counterpart |
| `unresolved_defined_term` | 0 | 0.0% | rejected because a referenced term is not defined here |

## Rejected at the gate

| Invariant | Rows |
|---|---:|
| `ratio_threshold_format` | 1 |

## Every miss

| Field | Outcome | Expected | Got |
|---|---|---|---|
| `covenants.9_1.defined_term` | `missing_field` | Consolidated Leverage Ratio | - |
| `covenants.9_2.defined_term` | `missing_field` | Consolidated Interest Coverage Ratio | - |
| `covenants.9_2.threshold` | `missing_field` | 2.50 to 1.00 | - |
| `covenants.9_3.defined_term` | `missing_field` | Consolidated Leverage Ratio | - |

---

Accuracy alone does not say whether the pipeline is improving. Read the
taxonomy: a run that trades `missing_field` for `wrong_value` has got
worse even if the headline number went up, because a wrong number with a
citation is harder to catch than a gap.
