---
name: sequential-thinking
description: "Dynamic, reflective problem-solving through structured sequential thoughts with support for branching, revision, and adaptive depth. Use this skill when: (1) Breaking down complex problems into steps, (2) Planning and design with room for revision, (3) Analysis that might need course correction, (4) Problems where the full scope is not clear initially, (5) Multi-step solutions requiring maintained context, (6) Situations where irrelevant information must be filtered out, (7) Any task benefiting from hypothesis generation, verification, and iterative refinement. Triggers: think through, step by step, break this down, sequential thinking, reason through, analyze step by step, think carefully, or when a problem clearly benefits from structured multi-step reasoning."
---

# 🧠 Sequential Thinking (Python CLI + Planning + MCP Parity)

## 🚨 Core Rule (NON-NEGOTIABLE)

> AI MUST NOT reason in free text.  
> ALL reasoning MUST happen via CLI calls to `think.py`.

---

# 🎯 Purpose

This skill enforces:
- Step-by-step reasoning
- Deterministic execution
- No hallucinated jumps
- Full traceability
- Planning-first approach

---

# ⚙️ Execution Model

```
RESET → PLAN → STEP → THOUGHT → STORE → VALIDATE → NEXT → LOOP → TERMINATE
```

---

# 📂 Script Location

```
think.py
```

---

# 🧩 Workflow (STRICT)

## 1. Reset (MANDATORY)

```bash
python think.py --reset
```

---

## 2. Set Plan (MANDATORY)

```bash
python think.py --setPlan "Check RTP,Analyze reels,Validate RNG,Check bonus"
```

---

## 3. Submit Thought

```bash
python think.py \
  --thought "RTP is -0.8% lower than expected" \
  --thoughtNumber 1 \
  --totalThoughts 5 \
  --nextThoughtNeeded true
```

---

## 4. Advance Step

```bash
python think.py --nextStep
```

---

## 5. Revision

```bash
python think.py \
  --thought "Correction: issue is from bonus" \
  --thoughtNumber 3 \
  --totalThoughts 5 \
  --nextThoughtNeeded true \
  --isRevision --revisesThought 1
```

---

## 6. Branch

```bash
python think.py \
  --thought "Alternative: RNG issue" \
  --thoughtNumber 4 \
  --totalThoughts 7 \
  --nextThoughtNeeded true \
  --branchFromThought 2 \
  --branchId rng-check
```

---

## 7. Extend Depth

```bash
python think.py \
  --thought "Need deeper analysis" \
  --thoughtNumber 6 \
  --totalThoughts 8 \
  --nextThoughtNeeded true \
  --needsMoreThoughts
```

---

## 8. Status

```bash
python think.py --status
```

---

# 📊 Output Format

```
💭 Thought 3/7
📍 Step: Analyze reels
RNG distribution looks incorrect
```

---

# 📌 Parameters

| Parameter | Type | Required | Description |
|----------|------|----------|------------|
| --thought | string | yes | Thought content |
| --thoughtNumber | int | yes | Sequential step |
| --totalThoughts | int | yes | Estimated steps |
| --nextThoughtNeeded | bool | yes | Continue or stop |
| --isRevision | flag | no | Revision |
| --revisesThought | int | no | Target thought |
| --branchFromThought | int | no | Branch start |
| --branchId | string | no | Branch ID |
| --needsMoreThoughts | flag | no | Extend depth |

---

# 🔥 Behavioral Rules (STRICT)

1. MUST reset before session  
2. MUST define plan before thinking  
3. MUST follow sequential numbering  
4. MUST NOT skip thoughts  
5. MUST attach plan step to every thought  
6. MUST use revision for correction  
7. MUST use branching for alternatives  
8. MUST NOT reason outside CLI  
9. MUST terminate explicitly  
10. MUST ignore irrelevant information  
11. MUST validate hypotheses against prior thoughts  

---

# 🧠 State Management

- Stored in `.think_state.json`
- `thoughtHistory` is append-only
- `branches` tracks alternative paths
- Plan state persists across steps

---

# 🚀 Example (RTP Debugging)

## Plan
```
1. Check RTP
2. Analyze reels
3. Validate RNG
4. Check bonus
```

## Execution
```
Thought 1 → RTP mismatch
Thought 2 → Reel weights OK
Thought 3 → RNG issue found
```

---

# 🧠 Key Insight

This is NOT a prompt.

This is a **deterministic reasoning protocol for AI agents**.
