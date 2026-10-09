# RAG-Based Metadata Mapping Plan

## Problem Statement
Different RTP reports use different field names for the same semantic meaning:
- "Total plays" vs "Total number of plays" vs "Num plays" vs `TOTAL_SPINS`
- "Total paid" vs "Total amount paid" vs "Total payout" vs `TOTAL_WIN`

Current solution: hardcoded aliases + `difflib` fuzzy matching in `MetadataMapper`.
It works, but it is **string matching pretending to be semantics**, and it already
produces silently wrong results on a real report.

---

## Evidence: What Actually Goes Wrong Today

Measured against `rich_little_piggies.xlsx` (OGA custom tab-delimited format).

### Raw report header
```
TOTAL_SPINS     1201999130
TOTAL_STAKE     750000000.00
STAKE_SPINS     1000000000
MAX_WIN         15479.950
TOTAL_WIN       722699953.150
HIT_RATE        2.8742
RTP             96.3600
STD_DEV         17.4094
```

### What the current mapper produced
```json
{
  "Total number of plays": "1201999130",
  "Total amount staked":   "1000000000",
  "Total amount paid":     "722699953.150",
  "Game RTP":              "96.3600"
}
```

### Failure mode 1 — Alias collision producing a wrong value (critical)
`STAKE_SPINS` is a **count of paid spins**. It was aliased to `Total amount staked`,
an **amount**, and it overwrote the correct `TOTAL_STAKE` value.

| Field | Raw value | Mapped to | Correct? |
|---|---|---|---|
| `TOTAL_STAKE` | 750,000,000.00 | *(lost)* | — |
| `STAKE_SPINS` | 1,000,000,000 | `Total amount staked` | **No** |

This is the dangerous class of error: no exception, no warning, just a wrong number
flowing into RTP math.

### Failure mode 2 — Silent field loss (low recall)
4 of 8 metadata rows were dropped because fuzzy similarity never cleared threshold:

| Dropped | Should map to |
|---|---|
| `MAX_WIN` | Largest win |
| `HIT_RATE` | Win Hit Rate |
| `STD_DEV` | Win standard deviation |
| `TOTAL_STAKE` | Total amount staked |

Lexical distance between `STD_DEV` and `Win standard deviation` is near zero, yet
they are the same concept. This is exactly where semantics beat strings.

### Failure mode 3 — Last-write-wins has no tie-break
Two candidates mapped to the same canonical key, and the parser simply kept the last
one. There is no notion of "which candidate is more likely correct".

---

## The Key Insight: Validate Mappings With Arithmetic, Not Just Similarity

RTP reports are **internally redundant**. That redundancy is a free correctness oracle,
and it is far stronger evidence than any embedding similarity score.

Applying it to the data above:

```
TOTAL_WIN / TOTAL_STAKE  = 722,699,953.15 / 750,000,000 = 0.96360  → matches RTP 96.3600  ✓
TOTAL_WIN / STAKE_SPINS  = 722,699,953.15 / 1,000,000,000 = 0.72270 → contradicts RTP      ✗
```

The wrong mapping is **provably** wrong in one division. No LLM required.

**Design consequence:** retrieval/LLM proposes candidates; invariants decide.
Semantic similarity should never be the final authority when an invariant is available.

### Invariants worth encoding
| Invariant | Catches |
|---|---|
| `total_paid / total_staked ≈ reported_rtp` | stake/win/RTP mix-ups |
| `total_staked ≈ paid_spins × bet` | count-vs-amount confusion |
| `paid_spins ≤ total_spins` | free-spin vs paid-spin swap |
| `Σ component_rtp ≈ total_rtp` | component mis-attribution |
| integer-valued field mapped to an amount key | unit/type mismatch |

---

## Recommended Design: Layered Resolver With Invariant Gate

The ordering matters. Cheap/deterministic layers run first; the LLM is a last resort;
and **every** candidate mapping must survive the invariant gate before it is accepted.

```
            ┌─ L0  Exact alias lookup            (O(1), confidence 1.00)
            │
 raw field ─┼─ L1  Type/unit prefilter           (drops impossible candidates)
            │
            ├─ L2  Lexical + token fuzzy match   (confidence 0.70–0.95)
            │
            └─ L3  Semantic retrieval + LLM      (only for L0–L2 misses)
                         │
                         ▼
              ┌───────────────────────────────┐
              │  INVARIANT GATE (mandatory)   │
              │  reject/re-rank on arithmetic │
              └───────────────────────────────┘
                         │
                         ▼
                  accepted mapping + cache
```

### L1 is the cheapest fix for the bug we found
A type/unit prefilter alone would have prevented the `STAKE_SPINS` collision:

```
canonical "Total amount staked" has unit = currency
STAKE_SPINS value 1000000000 is integer-valued, and a sibling field
  named *_SPINS exists → unit = count
count ≠ currency → candidate rejected before scoring
```

No embeddings, no API call, no knowledge base. This should ship first.

### Resolution contract

```python
@dataclass
class FieldMapping:
    raw_name: str            # "STAKE_SPINS"
    canonical: str | None    # "Plays with stake"
    confidence: float        # 0.0 - 1.0
    layer: str               # "L0" | "L1" | "L2" | "L3"
    unit: str                # "count" | "currency" | "ratio" | "percent"
    invariants_passed: list[str]
    invariants_failed: list[str]
    needs_review: bool
```

Every mapping is explainable: which layer decided it, and which invariants backed it.

### Conflict resolution (replaces last-write-wins)
When two raw fields claim the same canonical key:
1. Prefer the candidate whose unit matches the canonical unit.
2. Prefer the candidate that satisfies more invariants.
3. Prefer the higher confidence score.
4. If still tied → keep neither, emit a warning, flag for review.

---

## RAG Architecture for Metadata Mapping

### 1. **Knowledge Base Layer**
Store canonical field definitions + examples:

```
Field Name: "Total number of plays"
Aliases: ["Total plays", "Num plays", "Play count", ...]
Description: "Total count of game rounds played"
Example Values: [100000, 5000000, 250000]
Field Type: integer
Engine Sources: ["OGA", "Pragmatic", "NetEnt", ...]

Field Name: "Total amount paid"
Aliases: ["Total paid", "Total payout", "Total win", ...]
Description: "Sum of all payouts/wins across all rounds"
Example Values: [95000.00, 4750000.50, ...]
Field Type: float
Engine Sources: ["OGA", "Pragmatic", ...]
```

### 2. **Retriever Component**
When encountering an unknown field, retrieve relevant context:

```
Input: "Total plays" (from report)
    ↓
Query Embedding: encode("Total plays")
    ↓
Vector DB Search: Find k=3 most similar canonical fields
    ↓
Retrieved Context:
  1. "Total number of plays" (similarity: 0.92)
  2. "Play count" (similarity: 0.88)
  3. "Plays with stake" (similarity: 0.65)
```

**Vector Store Options:**
- FAISS (offline, fast, local)
- Pinecone (cloud, serverless)
- Weaviate (self-hosted)
- Milvus (open source)

### 3. **Generator Component (LLM-based)**
Use LLM to determine semantic equivalence:

```
Prompt Template:
---
KnowledgeBase Retrieval:
Canonical Field 1: "Total number of plays"
  - Description: Total count of game rounds
  - Engine Examples: [100000, 5000000]
  
Field from Report: "Total plays"
  - Context: [other fields in report...]
  - Value Sample: "12345"

Question: Are these the same field?
Answer: YES, with confidence 0.95

Reasoning: "Total plays" is a common abbreviation 
for "Total number of plays" - both represent 
the count of game rounds.
---
```

**LLM Options:**
- Claude (via API) - semantic reasoning
- GPT-4o mini (cost-effective)
- Local LLM (Ollama/LLaMA2) - no API dependency
- Open Router (multi-model access)

### 4. **Caching Layer**
Avoid repeated LLM calls:

```
MetadataCache:
{
  "Total plays" → "Total number of plays" (cached, confidence: 0.95),
  "Total payout" → "Total amount paid" (cached, confidence: 0.92),
}

When confidence < 0.7, trigger human review workflow
```

---

## Implementation Strategy

### Phase 1: Knowledge Base Setup (Static)
```
metadata/
  ├── canonical_fields.json       # Define all known fields
  ├── field_examples.jsonl        # Examples from various engines
  └── mappings_verified.json      # Human-verified mappings
```

### Phase 2: Retrieval System (L3a, semantic search)

Important: embed the **description**, not just the name. `STD_DEV` is lexically far
from `Win standard deviation` but semantically adjacent to its description.

```python
# Pseudocode
class MetadataRetriever:
    def __init__(self, kb_path):
        self.kb = load_knowledge_base(kb_path)
        self.embeddings = SentenceTransformers("all-MiniLM-L6-v2")
        # index over "<name>. <description>. aliases: <...>"
        self.faiss_index = build_faiss_index(self.kb, field="embed_text")

    def retrieve(self, unknown_field: str, top_k=3):
        query = expand_abbreviations(unknown_field)   # STD_DEV -> "std dev standard deviation"
        unknown_embedding = self.embeddings.encode(query)
        return self.faiss_index.search(unknown_embedding, top_k)
```

### Phase 3: Adjudicator (L3b, last resort)

Note the return type: this proposes **ranked candidates with reasoning**, it does not
return a final answer. The invariant gate still has the final say.

```python
# Pseudocode
class SemanticAdjudicator:          # distinct from the existing MetadataMapper
    def __init__(self, llm_client, retriever, cache):
        self.llm = llm_client
        self.retriever = retriever
        self.cache = cache          # key must include KB version

    def propose(self, unknown_field: str, report_context=None) -> list[Candidate]:
        key = (self.cache.kb_version, unknown_field)
        if key in self.cache:
            return self.cache[key]

        candidates = self.retriever.retrieve(unknown_field)

        ranked = self.llm.rank_candidates(
            unknown_field,
            candidates,
            report_context,          # sibling field names + sample values
        )                            # -> [Candidate(canonical, confidence, reasoning)]

        self.cache[key] = ranked
        return ranked
```

**Cache invalidation:** entries are keyed by KB version. Editing the knowledge base
must not leave stale mappings behind — that would reintroduce exactly the kind of
silent wrongness this plan exists to eliminate.

### Phase 4: Pipeline Integration
```python
# In pipeline.py
def analyze(file_path: str) -> Dict:
    report = parse_report(file_path)

    resolver = FieldResolver(kb_path="metadata/knowledge_base/")
    mappings = resolver.resolve_all(
        report["metadata"],
        context={"engine": detect_engine(report)},
    )

    # Similarity only proposes; arithmetic decides.
    mappings, warnings = InvariantGate(kb.invariants).apply(mappings)

    normalized_meta = {m.canonical: m.value for m in mappings if m.canonical}

    return {
        **analyze_normalized(report, normalized_meta),
        "field_mappings": [m.to_dict() for m in mappings],
        "warnings": warnings,
    }
```

---

## Where Each Layer Earns Its Keep

| Layer | Deterministic | Offline | Cost | Best at | Blind to |
|---|---|---|---|---|---|
| L0 exact alias | yes | yes | ~0 | known vocabulary | anything new |
| L1 unit/type filter | yes | yes | ~0 | **count-vs-amount errors** | synonyms |
| L2 lexical fuzzy | yes | yes | ~0 | typos, spacing, case | abbreviations (`STD_DEV`) |
| L3a embeddings | mostly | yes | low | abbreviations, synonyms | domain semantics |
| L3b LLM | no | no | high | genuinely novel fields | arithmetic truth |
| Invariant gate | yes | yes | ~0 | **verifying all of the above** | fields with no invariant |

Read this table as the argument against jumping straight to RAG: the bug we actually
hit lives in the L1 row, and the verification we actually need lives in the last row.
Neither is a retrieval problem.

### Residual risk after L0–L2 + gate
Fields with no invariant coverage (`MAX_WIN`, `STD_DEV`, `HIT_RATE`) can still be
mis-mapped without detection. That is the genuine, narrow case for L3 — and it is also
why those fields should be mapped conservatively and surfaced for review rather than
silently accepted.

---

## Knowledge Base Schema

Units and invariants are first-class — that is the main change from the original draft.

```json
{
  "canonical_fields": [
    {
      "id": "total_plays",
      "name": "Total number of plays",
      "description": "Total count of game rounds played, including free spins",
      "field_type": "integer",
      "unit": "count",
      "aliases": ["Total plays", "Num plays", "Play count", "TOTAL_SPINS"],
      "example_values": [100000, 1201999130],
      "engines": ["OGA", "Pragmatic", "NetEnt"],
      "validation_rules": ["positive_integer", "gte:paid_spins"]
    },
    {
      "id": "paid_spins",
      "name": "Plays with stake",
      "description": "Count of rounds that consumed a stake (excludes free spins)",
      "field_type": "integer",
      "unit": "count",
      "aliases": ["Plays with stake", "STAKE_SPINS"],
      "example_values": [1000000000],
      "engines": ["OGA"],
      "validation_rules": ["positive_integer", "lte:total_plays"]
    },
    {
      "id": "total_staked",
      "name": "Total amount staked",
      "description": "Sum of currency wagered across all paid rounds",
      "field_type": "float",
      "unit": "currency",
      "aliases": ["Total staked", "Total bet", "TOTAL_STAKE"],
      "example_values": [750000000.00],
      "engines": ["OGA", "Pragmatic"],
      "validation_rules": ["non_negative_float", "approx:paid_spins*bet"]
    },
    {
      "id": "total_paid",
      "name": "Total amount paid",
      "description": "Sum of all payouts across rounds",
      "field_type": "float",
      "unit": "currency",
      "aliases": ["Total paid", "Total payout", "TOTAL_WIN"],
      "example_values": [722699953.15],
      "engines": ["OGA", "Pragmatic"],
      "validation_rules": ["non_negative_float"]
    }
  ],
  "invariants": [
    {
      "id": "rtp_consistency",
      "expr": "total_paid / total_staked",
      "equals": "reported_rtp",
      "tolerance": 0.0005,
      "severity": "error"
    },
    {
      "id": "stake_from_spins",
      "expr": "paid_spins * bet",
      "equals": "total_staked",
      "tolerance": 0.01,
      "severity": "warning"
    },
    {
      "id": "spin_counts_ordered",
      "expr": "paid_spins",
      "lte": "total_plays",
      "severity": "error"
    }
  ]
}
```

---

## Implementation Roadmap

Ordered by **value per unit of effort**, derived from the failures measured above.
Note that the two highest-value steps require no RAG at all.

| # | Work | Fixes | Needs LLM? | Effort |
|---|---|---|---|---|
| 1 | Unit/type tagging + prefilter (L1) | Failure mode 1 (wrong value) | No | S |
| 2 | Invariant gate + conflict resolution | Failure modes 1 & 3 | No | S |
| 3 | Token-aware matching (`STD_DEV` → `std`+`dev`) | Failure mode 2 (recall) | No | S |
| 4 | Report-level mapping report + warnings | Observability | No | S |
| 5 | Canonical KB extracted to JSON | Maintainability | No | M |
| 6 | Embedding retrieval over KB (L3a) | Unknown fields | No* | M |
| 7 | LLM adjudication for low-confidence (L3b) | Long tail | Yes | M |
| 8 | Cache + human review loop | Cost/latency/accuracy | Yes | M |

\* local sentence-transformers, no API dependency.

### Acceptance criteria

Each step is only "done" when measurable on real reports:

| Metric | Today | Target |
|---|---|---|
| Metadata fields correctly mapped (`rich_little_piggies`) | 3 / 8 | 8 / 8 |
| Silently wrong values | 1 | 0 |
| Silently dropped fields | 4 | 0 (dropped → explicit warning) |
| `rtp_consistency` invariant checked | no | yes, on every run |
| Mappings carrying an explanation | no | yes, all |

The first regression test should be exactly the bug found here:
`STAKE_SPINS` must not map to `Total amount staked`, and `TOTAL_STAKE` must survive.

---

## Integration Points In This Codebase

| Concern | Location today | Change |
|---|---|---|
| Alias table | `MetadataMapper.ALIASES` in [report_parser/parser.py](../report_parser/parser.py) | move to KB JSON, add units |
| Field normalization call | `BaseParser.parse` in [report_parser/parser.py](../report_parser/parser.py) | return `FieldMapping`, not `str` |
| Required-field extraction | `analyze()` in [slotdebugger/pipeline.py](../slotdebugger/pipeline.py) | run invariant gate here |
| Analysis output | `analyze()` return dict | add `field_mappings` + `warnings` |
| Command surface | [slotdebugger/registry.py](../slotdebugger/registry.py) | add `review-mappings` command later |

Keeping `FieldMapping` as the parser's return type is what makes every later layer
(embedding, LLM, review loop) a drop-in addition rather than a rewrite.

---

## Decision Point

**Do steps 1–4 regardless.** They are deterministic, offline, cheap, and they fix the
only *correctness* bug we have actually observed. RAG would not have fixed it faster.

**Add steps 6–8 (real RAG) when:**
- reports arrive from engines whose field vocabulary you do not control, and
- manually extending the alias table has become the bottleneck, and
- you can tolerate LLM latency/cost on the cold path only.

**Do not** let semantic similarity make the final call on a field that participates in
an arithmetic invariant. Retrieval proposes; arithmetic disposes.
