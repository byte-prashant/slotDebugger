# 🧠 Autonomous RTP Debugging Agent (Sequential Thinking System)

---

## 🚀 Overview

Deterministic AI reasoning system for slot RTP debugging using:
- CLI thinking engine (think.py)
- LLM reasoning
- CSV RTP reports
- Structured planning

---

# 🏗️ HIGH-LEVEL FLOWCHART

```
CSV → PARSE → NORMALIZE → PLAN → AGENT LOOP → LLM → THINK.PY → TOOLS → OUTPUT
```

Detailed:

```
        CSV FILE
            ↓
     CSV READER
            ↓
     RTP NORMALIZER
            ↓
     PLAN GENERATOR
            ↓
      AGENT LOOP
            ↓
   ┌────────┴────────┐
   ↓                 ↓
LLM ENGINE     THINK.PY (CLI)
   ↓                 ↓
   └──────→ VALIDATION
                    ↓
             TOOL EXECUTION
                    ↓
             MEMORY / STATE
                    ↓
             FINAL REPORT
```

---

# 🔍 COMPONENT FLOWS

## 📂 CSV READER

```
CSV
 ↓
Read File
 ↓
Parse Rows
 ↓
Extract:
  - feature
  - RTP
 ↓
Structured JSON
```

---

## 📊 RTP NORMALIZER

```
Raw Data
 ↓
Group Features
 ↓
Calculate Diff
 ↓
Sort by Impact
 ↓
Critical Issues
```

---

## 🧠 PLAN GENERATION

```
Input Issues
 ↓
Detect Type
 ↓
Generate Plan
 ↓
Set Plan (CLI)
```

---

## 🔁 AGENT LOOP

```
Start
 ↓
Get State
 ↓
Get Current Step
 ↓
Build Prompt
 ↓
LLM Generate Thought
 ↓
Validate
 ↓
think.py execution
 ↓
Tool execution
 ↓
Check nextThoughtNeeded
 ↓
YES → LOOP
NO → END
```

---

## 🤖 LLM FLOW

```
Input:
- Step
- RTP Data
- History
 ↓
LLM
 ↓
Output Thought JSON
```

---

## ⚙️ THINK.PY FLOW

```
CLI Input
 ↓
Validate
 ↓
Attach Plan Step
 ↓
Save State
 ↓
Output
```

---

## 🧪 TOOL EXECUTION

```
Thought
 ↓
Map to Function
 ↓
Execute
 ↓
Return Result
 ↓
Feed to LLM
```

---

## 🔄 REVISION

```
Detect Error
 ↓
Revise Thought
 ↓
Update State
```

---

## 🌿 BRANCHING

```
Uncertainty
 ↓
Create Branch
 ↓
Parallel Reasoning
```

---

## 📤 FINAL OUTPUT

```
End Loop
 ↓
Aggregate Insights
 ↓
Root Cause
 ↓
Fix Suggestion
```

---

# 🧠 FINAL SYSTEM

```
CSV → Data → Plan → LLM → think.py → Tools → Feedback → Output
```

---

# 🔥 INSIGHT

This is a **controlled reasoning system**, not just AI.

