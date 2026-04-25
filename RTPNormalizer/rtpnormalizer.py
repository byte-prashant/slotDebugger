# Re-run the test suite cleanly

from typing import List, Dict
import unittest

class RTPDependencyResolver:
    def __init__(self, components: List[Dict]):
        self.components = components

    def build_dependency_graph(self) -> Dict:
        graph = {}
        for comp in self.components:
            name = comp["name"]
            if "total" in name:
                base = name.replace("_total", "")
                children = []
                for c in self.components:
                    if c["name"].startswith(base) and c["name"] != name:
                        children.append(c["name"])
                if children:
                    graph[name] = {"children": children}
        return graph

class RTPNormalizer:
    def __init__(self, components: List[Dict], metadata: Dict, dependency_graph: Dict = None):
        self.components = components
        self.total_plays = metadata["total_plays"]
        self.total_game_win = metadata["total_game_win"]
        self.dependency_graph = dependency_graph or RTPDependencyResolver(components).build_dependency_graph()

    def run(self):
        normalized = {}
        for c in self.components:
            name = c["name"]
            total_win = c["rtp"]
            freq = c["frequency"]
            normalized[name] = {
                "rtp": round(total_win / self.total_game_win, 6),
                "hit_rate": round(freq / self.total_plays, 6)
            }
        print(normalized)
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
        return sum(v["rtp"] for k, v in self.components.items() if "total" in k)

    def run(self):
        issues = self.validate_totals()
        total = self.total_rtp()
        return {
            "status": "OK" if not issues else "FAIL",
            "issues": issues,
            "total_rtp": round(total, 6)
        }

