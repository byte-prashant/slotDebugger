# RAG-Based Dynamic Component Mapping Plan
## For Any Game, Any Report, Any Feature

---

## Problem Statement

Different games have completely different component/feature names:
- **Game 1 (OGA)**: "base_game", "free_spins", "bonus_round"
- **Game 2 (Pragmatic)**: "Base Game", "Bonus Spins", "Feature"
- **Game 3 (NetEnt)**: "basegame", "freespins", "special_feature"
- **Game 4 (Custom)**: "main_game_win", "fs_feature", "bonus"

**Current problem:** Hardcoded aliases only work for known games. New games = manual updates.

**Goal:** System that learns component semantics and automatically maps from ANY report.

---

## Solution Architecture

### Phase 1: Dynamic Component Discovery

**Challenge:** Don't know what components exist until we see the report.

**Solution:** Component fingerprinting by semantic similarity

```
Report Components:
  "base_game": (40.0, 10)  ← What is this? Base game/main game?
  "free_spins": (50.0, 5)   ← Free spins feature?
  "mystery_bonus": (60.0, 2) ← Unknown! Could be bonus/feature/etc

Component Fingerprinting:
  1. Extract all component names from report
  2. For each unknown: create fingerprint
     - Name similarity to known bases
     - Value characteristics (RTP, frequency)
     - Context from report metadata
  3. Cluster similar components
  4. Map clusters to canonical types
```

**Fingerprint Features:**
```python
ComponentFingerprint = {
    "name": str,                    # "base_game"
    "name_tokens": set,             # {"base", "game"}
    "rtp": float,                   # 40.0
    "frequency": int,               # 10
    "rtp_percentile": float,        # 40th percentile among components
    "frequency_percentile": float,  # 10th percentile
    "engine": str,                  # "OGA" (if detected)
    "is_core_component": bool,      # Core (base game) or feature?
}
```

---

### Phase 2: Core vs. Feature Classification

**Key insight:** Every game has core and feature components.

**Classification rules:**

```
1. Core Components (appear in ~95% of games):
   - Base game / normal game / main game
   - Typically: highest frequency, moderate RTP
   - Usually: name contains "base", "main", "normal"

2. Feature Components (vary by game):
   - Free spins, Bonus games, Jackpots, etc.
   - Typically: lower frequency, variable RTP
   - Name contains feature keywords

3. Statistical signals:
   - Core: frequency >> other components
   - Features: frequency < core, RTP varies
```

**Algorithm:**

```
For each component:
  score = 0
  
  # Name-based signals
  if name contains ("base", "main", "normal", "game"):
    score += 20
  if name contains ("free", "bonus", "feature", "multi"):
    score -= 20
  
  # Statistical signals
  if frequency > median_frequency * 0.8:
    score += 15  # High frequency → likely core
  if frequency < median_frequency * 0.3:
    score -= 15  # Low frequency → likely feature
  
  # RTP signals
  if rtp near average_rtp:
    score += 10  # Core games have average RTP
  
  return "CORE" if score > threshold else "FEATURE"
```

---

### Phase 3: Semantic Mapping with Knowledge Base

**3-tier knowledge base:**

```
Tier 1: CANONICAL TYPES (universal)
  - "base_game" (the ONE core component)
  - "free_spins"
  - "bonus_game"
  - "scatter"
  - "jackpot"
  - "multiplier"
  - "respin"
  - "wild"

Tier 2: ENGINE VARIANTS (OGA, Pragmatic, NetEnt, etc.)
  OGA_variants = {
    "base_game": ["base_game", "base_win", "main_game"],
    "free_spins": ["fs", "free_spins", "bonus_spins"],
  }
  
  Pragmatic_variants = {
    "base_game": ["Base Game", "Main Game"],
    "free_spins": ["Free Spins", "Bonus Spins"],
  }

Tier 3: GAME-SPECIFIC OVERRIDES (learned from reports)
  rich_little_piggies = {
    "base_spin": "base_game",         # Game-specific name
    "pig_feature": "free_spins",      # Game-specific name
    "fat_bonus": "bonus_game",
  }
```

**Mapping strategy (3-step fallback):**

```
Step 1: Exact match in canonical/engine/game tier
  ✓ Fast, deterministic
  ✗ Limited to known mappings

Step 2: Semantic similarity + stats
  - Compute similarity(unknown_name, known_canonicals)
  - Apply statistical fingerprint scoring
  - Return if confidence > 0.85
  ✓ Handles variations
  ✗ Can be noisy

Step 3: Contextual clustering
  - Group ALL unknown components by similarity
  - Use report RTP/frequency patterns
  - Human confirmation (low confidence)
  ✓ Handles completely new features
  ✗ Requires confirmation
```

---

### Phase 4: Report Format Agnostic

**Works with any report structure:**

```
CSV Report:
  base_game,Free Spins,mystery
  40.0|10, 50.0|5, 60.0|2

JSON Report:
  {
    "components": [
      {"feature": "base_game", "rtp": 40.0},
      {"feature": "Free Spins", "rtp": 50.0}
    ]
  }

XLSX Report (current):
  base_game    (40.0, 10)
  Free Spins   (50.0, 5)

Custom Report:
  BASE,FREESPINWIN,BONUSROUND
  40.0:10, 50.0:5, 60.0:2
```

**Parser adapts to format** → Extracts: {name, rtp, frequency}

---

### Phase 5: Learning and Caching

**Cache structure:**

```json
{
  "reports_processed": 150,
  "mappings": {
    "base_game": {
      "canonical": "base_game",
      "confidence": 1.0,
      "count": 145,
      "sources": ["OGA", "Pragmatic", "NetEnt", "custom"],
      "variants_seen": ["base_game", "Base Game", "basegame", "main_game"]
    },
    "mystery_feature_v1": {
      "canonical": "free_spins",
      "confidence": 0.92,
      "count": 3,
      "method": "semantic_similarity",
      "games": ["rich_little_piggies", "mystery_game_1"]
    },
    "unknown_component_xyz": {
      "canonical": null,
      "confidence": 0.45,
      "status": "needs_review",
      "observations": {
        "rtp_percentile": 85,
        "frequency_percentile": 20,
        "name_similarity": {"bonus": 0.6, "feature": 0.55}
      }
    }
  },
  "game_profiles": {
    "rich_little_piggies": {
      "engine": "OGA",
      "components": ["base_game", "free_spins", "bonus"],
      "mappings_confirmed": true
    }
  }
}
```

---

## Implementation Steps

### Step 1: Component Analysis Engine
```
INPUT: List of components from ANY report
  ↓
PROCESS:
  - Extract: name, rtp, frequency
  - Compute: fingerprints, statistics
  - Classify: core vs feature
  - Score: similarity to canonical types
  ↓
OUTPUT:
  [
    {
      "original": "base_game",
      "canonical": "base_game",
      "confidence": 1.0,
      "type": "CORE"
    },
    {
      "original": "free_spins",
      "canonical": "free_spins",
      "confidence": 0.95,
      "type": "FEATURE"
    },
    {
      "original": "mystery_bonus",
      "canonical": "bonus_game",
      "confidence": 0.82,
      "type": "FEATURE",
      "needs_review": true
    }
  ]
```

### Step 2: Knowledge Base Builder
```
knowledge_base/
  ├── canonical_types.json          # Universal component types
  ├── engine_variants.json          # Engine-specific aliases
  ├── learned_mappings.json         # Game-specific overrides
  ├── confidence_thresholds.json    # Decision boundaries
  └── component_statistics.json     # RTP/freq patterns
```

### Step 3: Semantic Mapper
```python
class DynamicComponentMapper:
    
    def map_components(self, report_components, engine=None):
        """
        Map ANY component list to canonical types.
        Works for any game, any report format.
        """
        fingerprints = self.compute_fingerprints(report_components)
        
        mappings = []
        for fp in fingerprints:
            # Try: exact match → engine variant → semantic match
            canonical, confidence = self.find_best_match(fp, engine)
            
            mappings.append({
                "original": fp.name,
                "canonical": canonical,
                "confidence": confidence,
                "type": "CORE" if confidence > 0.9 else "FEATURE",
                "needs_review": confidence < 0.7
            })
        
        return mappings
```

### Step 4: Feedback Loop
```
Report Analysis:
  1. Map components
  2. Flag low-confidence (< 0.7)
  3. Store in "review_queue"
  
Human Review CLI:
  $ slotdebug review-mappings
  
  Component: "pig_feature"
  Mapped to: "free_spins" (confidence: 0.68)
  Observations:
    - Name similarity: 0.42
    - RTP: 50.0 (matches free_spins pattern)
    - Frequency: 5 (matches free_spins pattern)
  
  ✓ Confirm
  ✗ Reject (suggest: "bonus_game")
  ? Unsure (needs more data)
  
  → Updates cache with human feedback
```

### Step 5: Integration with Pipeline
```python
# In pipeline.py
def analyze(file_path: str) -> Dict:
    report = parse(file_path)
    
    # Auto-detect engine
    engine = detect_engine(report.metadata)
    
    # Dynamic component mapping
    mapper = DynamicComponentMapper()
    component_mappings = mapper.map_components(
        report.components,
        engine=engine
    )
    
    # Separate core + features
    core_components = [c for c in component_mappings if c["type"] == "CORE"]
    feature_components = [c for c in component_mappings if c["type"] == "FEATURE"]
    
    # Flag for review if needed
    if any(c["needs_review"] for c in component_mappings):
        log_for_human_review(file_path, component_mappings)
    
    # Analyze with canonical names
    normalized = RTPNormalizer(
        canonical_components,
        metadata
    ).run()
    
    return {
        ...analysis...,
        "component_mappings": component_mappings,
        "human_review_required": bool(low_confidence),
    }
```

---

## Decision Matrix

```
Confidence | Action
-----------+-------------------------------------------------------
> 0.95    | AUTO-ACCEPT (exact match, engine variant, known game)
0.85-0.95 | AUTO-ACCEPT with cache update
0.7-0.85  | AUTO-ACCEPT + flag for next batch review
0.5-0.7   | PENDING (cache, ask human)
< 0.5     | REJECT (not enough signal, needs context)
```

---

## Advantages of This Approach

| Feature | Benefit |
|---------|---------|
| **Dynamic** | Works for unknown games automatically |
| **Semantic** | Understands meaning, not just strings |
| **Scalable** | Add new games without code changes |
| **Transparent** | Confidence scores explain decisions |
| **Learnable** | Human feedback improves over time |
| **Deterministic** | Same input → same output (via cache) |
| **Extensible** | New canonical types = 1 JSON update |

---

## Example Workflow

```
User runs: slotdebug analyze --file rich_little_piggies.xlsx

Step 1: Parse report
  Components: [base_game, free_spins, bonus, mystery_feature]

Step 2: Fingerprint
  base_game: {freq: 1000, rtp: 40.0, tokens: ["base", "game"]}
  free_spins: {freq: 50, rtp: 50.0, tokens: ["free", "spins"]}
  bonus: {freq: 20, rtp: 60.0, tokens: ["bonus"]}
  mystery: {freq: 5, rtp: 70.0, tokens: ["mystery", "feature"]}

Step 3: Classify + Map
  base_game → "base_game" (confidence: 1.0) ✓
  free_spins → "free_spins" (confidence: 0.98) ✓
  bonus → "bonus_game" (confidence: 0.92) ✓
  mystery → "jackpot" (confidence: 0.68) ⚠ (review needed)

Step 4: Output
  ✓ Analyzed with 3 high-confidence mappings
  ⚠ 1 component needs review
  → Suggest: slotdebug review-mappings

Step 5: Human confirms
  User says: mystery_feature is actually a "progressive_jackpot"
  → Caches: mystery_feature → jackpot (confidence: human_confirmed)
  → Next report with similar feature: auto-mapped
```

---

## Limitations & Mitigations

```
Limitation: Different RTP patterns between games
Mitigation: Statistical fingerprinting relative to THIS report

Limitation: Completely new component type (never seen before)
Mitigation: Tier 3 fallback + human review queue

Limitation: Component names that are ambiguous
Mitigation: Confidence < 0.7 → flag for human verification

Limitation: Reports without engine metadata
Mitigation: Infer from RTP/frequency patterns + name analysis
```

---

## Success Criteria

- ✓ Maps 100% of known game components correctly
- ✓ Handles 90%+ of new component variations automatically
- ✓ Provides confidence scores for all mappings
- ✓ Human review queue < 1% of components
- ✓ Zero code changes for new games
- ✓ Works with ANY report format (CSV/XLSX/JSON/custom)
- ✓ Learns from human feedback iteratively

