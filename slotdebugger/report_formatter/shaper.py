"""Rebuild a template's structure with the values measured in a report."""

from typing import Any, Dict, List, Optional, Tuple

from slotdebugger.report_formatter import naming, template as tpl

_UNAVAILABLE = object()  # a key we understood but the report cannot answer


def apply(template: Any, result: Dict) -> Tuple[Any, List[str]]:
    """Fill `template`'s shape with `result`'s values.

    Returns `(output, notes)`; `notes` records every component match made and every
    key that could not be resolved, for the caller to show the user.
    """
    shaper = Shaper(result, template)
    return shaper.walk(template, [], None), shaper.notes


class Shaper:
    """Walks the template once, resolving each key against the analysis.

    The walk carries the component currently in scope, so `{"FG1": {"rtp": ...}}`
    and `{"name": "FG1", "rtp": ...}` both resolve `rtp` against *that* component.
    """

    def __init__(self, result: Dict, template: Any):
        self.meta = result.get("metadata") or {}
        self.components = result.get("components") or {}
        self.analysis = result.get("analysis") or {}
        self.notes: List[str] = []
        self._by_flat = {naming.flat(n): n for n in self.components}
        self._matched: Dict[str, Optional[str]] = {}
        self.base = self._rtp_base()
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
                return self._rtp(self.components[name]["rtp"], places)
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
            return self._rtp(self.components[self._by_flat[naming.flat(key)]]["rtp"], places)
        kind = naming.role(key)
        if kind == "total":
            return self._rtp(self.analysis.get("total_rtp", 0.0), places)
        if kind == "bet":
            staked = _number(self.meta.get("Total amount staked"))
            plays = _number(self.meta.get("Total number of plays"))
            if staked and plays:
                return round(staked / plays, 4)
            self.notes.append(
                f"{_label(path)}: report has no stake/play count to derive the bet from (left null)")
            return _UNAVAILABLE
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
        data = self.components[comp]
        if attr == "rtp":
            return self._rtp(data["rtp"], places)
        if attr == "share":
            return round(data["rtp"], 6)
        if attr == "hit_rate":
            return round(data["hit_rate"], 6)
        return None

    # ---- component matching ----
    def _component(self, key: str, path: List[str]) -> Optional[str]:
        if key in self._matched:
            return self._matched[key]
        name, score = naming.best_component(key, self.components)
        if name is not None and score < naming.STRONG:
            self.notes.append(f"{_label(path)} -> {name} (uncertain match, score {score:.2f})")
        elif name is not None:
            self.notes.append(f"{_label(path)} -> {name}")
        self._matched[key] = name
        return name

    # ---- values ----
    def _rtp(self, share: float, places: Optional[int] = None) -> float:
        """A component's share of total win, on the template's scale and precision."""
        return round(share * self.base * self.scale,
                     self.places if places is None else places)

    def _rtp_base(self) -> float:
        """Overall RTP as a fraction; the normalizer's shares are relative to it."""
        for field in ("Total RTP", "Game RTP"):
            value = _number(self.meta.get(field))
            if value and value > 0:
                return value / 100 if value > 1.5 else value
        staked = _number(self.meta.get("Total amount staked"))
        paid = _number(self.meta.get("Total amount paid"))
        if staked and paid and 0.1 < paid / staked < 2:
            return paid / staked
        self.notes.append(
            "report metadata has no overall RTP (Total RTP / Game RTP / staked+paid); "
            "values are each component's share of total win, not RTP")
        return 1.0


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _label(path: List[str]) -> str:
    return ".".join(path) or "<root>"
