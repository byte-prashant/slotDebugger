# Design documents

Proposals and analysis. These describe work that is **planned or partially done** —
they are not a description of current behaviour. For what the tool does today, see
the [README](../README.md); for domain reference, see [knowledge/](../knowledge/README.md).

| Document | What it covers | Status |
|---|---|---|
| [PLAN_RAG_METADATA_MAPPING.md](PLAN_RAG_METADATA_MAPPING.md) | Why metadata field normalization produces wrong values, and a layered resolver with an arithmetic invariant gate to fix it | Analysis done, not implemented |
| [PLAN_DYNAMIC_COMPONENT_MAPPING.md](PLAN_DYNAMIC_COMPONENT_MAPPING.md) | Mapping component names across games that use different feature vocabularies | Proposed |
| [PLAN_PLUGIN_ARCHITECTURE.md](PLAN_PLUGIN_ARCHITECTURE.md) | Exposing commands to Claude/Cursor over MCP so natural language invokes them | Registry layer done; MCP not started |
| [HOW_REGISTRY_WORKS.md](HOW_REGISTRY_WORKS.md) | Walkthrough of the command registry added for the plugin work | Current |

Start with `PLAN_RAG_METADATA_MAPPING.md` — it documents a live correctness bug
(`STAKE_SPINS` overwriting `TOTAL_STAKE`) that affects real reports today.
