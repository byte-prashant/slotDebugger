from typing import List, Dict

class RTPDependencyResolver:
    def __init__(self, components: List[Dict]):
        self.components = components

    TOTAL_MARKER = "_total"

    def build_dependency_graph(self) -> Dict:
        """Map each `<prefix>_total_<suffix>` component to its `<prefix>_*` children.

        Children are components sharing the `<prefix>_` prefix, excluding the
        parent itself and any other total (which are parents in their own right).
        """
        graph = {}
        for comp in self.components:
            name = comp["name"]
            if self.TOTAL_MARKER not in name:
                continue
            prefix = name.split(self.TOTAL_MARKER, 1)[0]
            children = [
                c["name"] for c in self.components
                if c["name"].startswith(prefix + "_")
                and self.TOTAL_MARKER not in c["name"]
            ]
            if children:
                graph[name] = {"children": children}
        return graph

class RTPNormalizer:
    def __init__(self, components: List[Dict], metadata: Dict, dependency_graph: Dict = None,
                 measured: Dict = None):
        """`measured` maps a component name to `{"rtp", "hit_rate"}` computed elsewhere
        (the aggregate file's formulas); rows it does not cover are computed here directly."""
        self.measured = measured
        self.components = components
        self.total_plays = metadata["total_plays"]
        self.total_game_win = metadata["total_game_win"]
        self.dependency_graph = (RTPDependencyResolver(components).build_dependency_graph()
                                 if dependency_graph is None else dependency_graph)

    def run(self):
        normalized = {}
        for c in self.components:
            name = c["name"]
            if self.measured is not None and name in self.measured:
                normalized[name] = {
                    "rtp": round(self.measured[name]["rtp"], 6),
                    "hit_rate": round(self.measured[name]["hit_rate"], 6),
                }
                continue
            total_win = c["rtp"]
            freq = c["frequency"]
            normalized[name] = {
                "rtp": round(total_win / self.total_game_win, 6),
                "hit_rate": round(freq / self.total_plays, 6)
            }
        return {
            "normalized_components": normalized,
            "dependency_graph": self.dependency_graph
        }

class RTPAnalyzer:
    def __init__(self, normalized_data: Dict):
        self.components = normalized_data["normalized_components"]
        self.graph = normalized_data["dependency_graph"]

    def validate_totals(self):
        issues = []
        for parent, data in self.graph.items():
            children = data["children"]
            parent_val = self.components[parent]["rtp"]
            child_sum = sum(self.components[c]["rtp"] for c in children)
            if abs(parent_val - child_sum) > 0.001:
                issues.append(f"Mismatch in {parent}")
        return issues

    def total_rtp(self):
        return sum(v["rtp"] for k, v in self.components.items() if "_total" in k)

    def run(self):
        issues = self.validate_totals()
        total = self.total_rtp()
        return {
            "status": "OK" if not issues else "FAIL",
            "issues": issues,
            "total_rtp": round(total, 6)
        }

