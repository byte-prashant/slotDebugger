"""Rebuild a template's structure with the values measured in a report."""

from typing import Any, Dict, List, Optional, Tuple

import rtp_aggregator
from slotdebugger.report_formatter import naming, template as tpl

_UNAVAILABLE = object()  # a key we understood but the report cannot answer


def apply(template: Any, result: Dict) -> Tuple[Any, List[str]]:
    """Fill `template`'s shape with `result`'s values.

    Returns `(output, notes)`; `notes` records every component match made and every
    key that could not be resolved, for the caller to show the user.
    """
    shaper = Shaper(result, template)
    return shaper.walk(template, [], None), shaper.notes


def matched_components(template: Any, names: List[str], meta: Dict) -> List[str]:
    """Report rows the template's keys resolve to, in report order.

    Matching looks at names only, so it can run before any value exists; it is how the
    aggregate is limited to what the expected report mentions.
    """
    shaper = Shaper({"metadata": meta, "rows": names}, template)
    shaper.walk(template, [], None)
    hit = {n for n in shaper._matched.values() if n}
    return [n for n in names if n in hit]


class Shaper:
    """Walks the template once, resolving each key against the analysis.

    The walk carries the component currently in scope, so `{"FG1": {"rtp": ...}}`
    and `{"name": "FG1", "rtp": ...}` both resolve `rtp` against *that* component.
    """

    def __init__(self, result: Dict, template: Any):
        self.meta = result.get("metadata") or {}
        # every row the report has, so a key resolves to the same row whether or not the
        # aggregate (which may cover only some rows) was built yet
        self.rows = {n: None for n in (result.get("rows") or result.get("components") or {})}
        self.aggregate = (result.get("aggregate") or {}).get("components") or {}
        self.notes: List[str] = []
        self._by_flat = {naming.flat(n): n for n in self.rows}
        self._matched: Dict[str, Optional[str]] = {}
        self.scale = tpl.scale(template)
        self.places = tpl.default_places(self.scale)

    # ---- structure ----
    def walk(self, node: Any, path: List[str], comp: Optional[str]) -> Any:
        if isinstance(node, dict):
            comp = self._identify(node, path) or comp
            out = {}
            for key, value in node.items():
                here = path + [key]
                if isinstance(value, (dict, list)):
                    out[key] = self.walk(value, here, self._component(key, here) or comp)
                else:
                    out[key] = self._leaf(key, here, value, comp)
            return out
        if isinstance(node, list):
            return [
                self.walk(v, path + [str(i)], comp) if isinstance(v, (dict, list))
                else self._leaf(None, path + [str(i)], v, comp)
                for i, v in enumerate(node)
            ]
        return self._leaf(path[-1] if path else None, path, node, comp)

    def _identify(self, node: Dict, path: List[str]) -> Optional[str]:
        """`{"name": "FG1", "rtp": ...}` is about the component its name field points at."""
        for key, value in node.items():
            if naming.is_identifier(key) and isinstance(value, str):
                return self._component(value, path + [key])
        return None

    # ---- leaves ----
    def _leaf(self, key: Optional[str], path: List[str], placeholder: Any,
              comp: Optional[str]) -> Any:
        if key is None:
            return placeholder
        numeric = isinstance(placeholder, (int, float)) and not isinstance(placeholder, bool)
        places = tpl.places(placeholder, self.places)
        if naming.is_identifier(key) and not numeric:
            return placeholder  # a label naming the component, not a measurement
        if comp is not None:
            value = self._attribute(key, comp, places)
            if value is _UNAVAILABLE:
                return None
            if value is not None:
                return value
        value = self._scalar(key, path, places)
        if value is _UNAVAILABLE:
            return None
        if value is not None:
            return value
        if numeric:  # only go looking for a component when a number is being asked for
            name = self._component(key, path)
            if name is not None:
                value = self._component_rtp(name, places)
                return None if value is _UNAVAILABLE else value
        value = naming.best_metadata(key, self.meta)
        if value is not None:
            number = _number(value)
            return value if number is None else number
        if numeric:
            self.notes.append(f"{_label(path)}: nothing in the report matches this key (left null)")
            return None
        return placeholder  # labels, units and other non-measured text pass through

    def _scalar(self, key: str, path: List[str], places: int) -> Optional[Any]:
        """A key that means something other than one component's measurement."""
        if naming.flat(key) in self._by_flat:  # a component named exactly like the key wins
            self._matched[key] = self._by_flat[naming.flat(key)]
            return self._component_rtp(self._by_flat[naming.flat(key)], places)
        kind = naming.role(key)
        if kind == "total":
            return self._computed("total_rtp", path, places, self.scale)
        if kind == "bet":
            return self._computed("bet", path, places, 1)
        if kind == "plays":
            plays = _number(self.meta.get("Total number of plays"))
            if plays:
                return int(plays)
            self.notes.append(f"{_label(path)}: report has no play count (left null)")
            return _UNAVAILABLE
        return None

    def _attribute(self, key: str, comp: str, places: int) -> Optional[Any]:
        """A measurement of the component currently in scope."""
        attr = naming.attribute(key)
        if attr == "rtp":
            return self._component_rtp(comp, places)
        if attr == "share":
            return self._computed(rtp_aggregator.key(comp, "rtp"), [comp, attr], 6, 1)
        if attr == "hit_rate":
            return self._computed(rtp_aggregator.key(comp, "hit_rate"), [comp, attr], 6, 1)
        return None

    # ---- component matching ----
    def _component(self, key: str, path: List[str]) -> Optional[str]:
        if key in self._matched:
            return self._matched[key]
        name, score = naming.best_component(key, self.rows)
        if name is not None and score < naming.STRONG:
            self.notes.append(f"{_label(path)} -> {name} (uncertain match, score {score:.2f})")
        elif name is not None:
            self.notes.append(f"{_label(path)} -> {name}")
        self._matched[key] = name
        return name

    # ---- values ----
    def _component_rtp(self, name: str, places: int) -> Any:
        """A component's RTP exactly as the aggregate's formula computed it."""
        return self._computed(rtp_aggregator.key(name, "rtp_vs_stake"), [name], places, self.scale)

    def _computed(self, component: str, path: List[str], places: int, scale: float) -> Any:
        """A value the aggregate computed, or `_UNAVAILABLE`.

        Nothing is derived here: a value the aggregate has no formula for is left null
        and reported, never worked out from the report by an assumption.
        """
        value = self.aggregate.get(component)
        if value is None:
            self.notes.append(f"{_label(path)}: the aggregate has no {component!r} formula (left null)")
            return _UNAVAILABLE
        return round(value * scale, places)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _label(path: List[str]) -> str:
    return ".".join(path) or "<root>"
