"""
Anaplan Workspace Capacity Forecaster
======================================

Predicts the impact on Anaplan workspace size when an Admin adds/grows a
List, or adds Line Items / a new Module.

IMPORTANT ASSUMPTION / DISCLAIMER
----------------------------------
Anaplan does not publish the exact internal formula it uses to compute
workspace/tenant size (Anaplan calls this "workspace size" in the Admin
console, measured in GB, based on their internal Hyperblock engine).
This script implements a transparent, defensible APPROXIMATION model that
is commonly used for capacity planning:

    cells (per line item) = time periods
                             x members of each List it is dimensioned by
                             x versions

    * Line item has NO summary  -> count only LEAF list members and leaf
      time periods            (e.g. 36 x 41 x 53 = 78,228)
    * Line item has ANY summary -> count ALL list members including parents,
      and time summary periods (e.g. 39 x 58 x 59 = 133,458)

    Leaf members of a list = Item Count minus the parent members counted
    inside it (the Item Count of its Parent Hierarchy list, or 1 for a
    top-level item). Time: Month = 12 x calendar years; with a summary one
    total period per year is added (3 years: 36 without, 39 with).

    approx size (MB) = cells x bytes_per_cell(data_type) / 1,000,000

Bytes-per-cell values approximate Anaplan's internal storage per data type
and are only indicative: calibrate to the size Anaplan reports for your
model before relying on MB figures. Cell counts are the reliable output.

Usage
-----
Run directly for a self-contained demo with sample data:

    python3 anaplan_workspace_forecaster.py

Or import and use programmatically:

    from anaplan_workspace_forecaster import (
        parse_lists, parse_line_items, compute_workspace,
        add_new_list, grow_existing_list, add_line_items, add_new_module,
        format_report,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional
import copy
import re


# ---------------------------------------------------------------------------
# Reference tables (editable — tune these to match your own tenant if you
# have better empirical numbers from Anaplan's workspace usage screen)
# ---------------------------------------------------------------------------

CALENDAR_YEARS = 3          # years in the model calendar (drives time periods)
PERIODS_PER_YEAR = {"month": 12, "quarter": 4, "year": 1, "week": 52, "day": 365}

BYTES_PER_CELL = {
    "number": 8,
    "boolean": 1,
    "text": 16,
    "list": 4,
    "time": 4,
    "currency": 8,
    "none": 4,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ListDef:
    name: str
    count: int
    hierarchy: str = "Flat"
    leaf: Optional[int] = None   # leaf members (count minus parent members)
    top_level: str = ""
    parent_hierarchy: str = ""
    selective_access: bool = False
    production_data: bool = False
    managed_by: str = ""
    has_workflow: bool = False
    numbered: bool = False
    is_organization: bool = False
    category: str = ""
    ref_applies_to: str = ""
    ref_format: str = ""
    ref_formula: str = ""
    next_item_index: Optional[int] = None
    use_top_level_default: bool = False
    delegated: bool = False


@dataclass
class LineItem:
    module: str
    name: str
    applies_to: List[str] = field(default_factory=list)  # list names (dimensions)
    data_type: str = "Number"
    summary: str = "None"
    time_periods: int = 0   # legacy: explicit leaf period count (0 = none)
    time_scale: str = ""    # month / quarter / year / week / day (preferred)
    versions: int = 1


@dataclass
class LineItemResult:
    module: str
    name: str
    dimension_size: int
    cells: float
    size_mb: float


@dataclass
class ModuleResult:
    module: str
    cells: float
    size_mb: float
    line_items: int


@dataclass
class WorkspaceResult:
    total_cells: float
    total_size_mb: float
    modules: Dict[str, ModuleResult]
    line_item_results: List[LineItemResult]


# ---------------------------------------------------------------------------
# Parsing helpers — accepts tab or comma separated pasted text, header
# row optional (auto-detected).
# ---------------------------------------------------------------------------

def _split_row(row: str) -> List[str]:
    row = row.strip()
    if "\t" in row:
        parts = row.split("\t")
    else:
        parts = row.split(",")
    return [p.strip() for p in parts]


# Column order matches Anaplan's native List Settings export:
# Name, Top Level, Parent Hierarchy, Selective Access, Production Data, Managed By,
# Has Workflow, Properties, Subsets, Numbered, Display Name Property, Is Organization,
# Category, Data Tags, Item Count, Notes, Referenced in Applies To, Referenced as Format,
# Referenced in Formula, Next item index, Use Top Level As Default Page, Delegated
LIST_EXPORT_COLUMNS = [
    "name", "top_level", "parent_hierarchy", "selective_access", "production_data",
    "managed_by", "has_workflow", "properties", "subsets", "numbered",
    "display_name_property", "is_organization", "category", "data_tags", "item_count",
    "notes", "ref_applies_to", "ref_format", "ref_formula", "next_item_index",
    "use_top_level_default", "delegated",
]
_LIST_BOOL_COLUMNS = {
    "selective_access", "production_data", "has_workflow", "numbered",
    "is_organization", "use_top_level_default", "delegated",
}


def parse_lists(text: str) -> Dict[str, ListDef]:
    """Parse pasted List info in Anaplan's native List Settings export format
    (22 tab-separated columns, header row optional — see LIST_EXPORT_COLUMNS)."""
    lists: Dict[str, ListDef] = {}
    for raw_line in text.strip().splitlines():
        if not raw_line.strip():
            continue
        cols = _split_row(raw_line)
        if cols[0].strip().lower() == "name":
            continue  # header row
        if not cols[0].strip():
            continue

        values: Dict[str, object] = {"name": cols[0]}
        for i, key in enumerate(LIST_EXPORT_COLUMNS):
            if key == "name":
                continue
            raw = cols[i] if i < len(cols) else ""
            values[key] = (raw.strip().upper() == "TRUE") if key in _LIST_BOOL_COLUMNS else raw.strip()

        try:
            item_count = int(re.sub(r"[,\s]", "", str(values.get("item_count", "0") or "0")))
        except ValueError:
            item_count = 0

        try:
            next_item_index = int(values.get("next_item_index") or 0) or None
        except ValueError:
            next_item_index = None

        lists[values["name"]] = ListDef(
            name=values["name"],
            count=item_count,
            hierarchy="Parent-Child" if values.get("parent_hierarchy") else "Flat",
            top_level=values.get("top_level", ""),
            parent_hierarchy=values.get("parent_hierarchy", ""),
            selective_access=bool(values.get("selective_access")),
            production_data=bool(values.get("production_data")),
            managed_by=values.get("managed_by", ""),
            has_workflow=bool(values.get("has_workflow")),
            numbered=bool(values.get("numbered")),
            is_organization=bool(values.get("is_organization")),
            category=values.get("category", ""),
            ref_applies_to=values.get("ref_applies_to", ""),
            ref_format=values.get("ref_format", ""),
            ref_formula=values.get("ref_formula", ""),
            next_item_index=next_item_index,
            use_top_level_default=bool(values.get("use_top_level_default")),
            delegated=bool(values.get("delegated")),
        )
    compute_leaf_counts(lists)
    return lists


def compute_leaf_counts(lists: Dict[str, ListDef]) -> None:
    """leaf = Item Count - parent members counted inside it."""
    for ld in lists.values():
        if ld.parent_hierarchy:
            parents = lists[ld.parent_hierarchy].count if ld.parent_hierarchy in lists else 0
        else:
            parents = 1 if ld.top_level else 0
        ld.leaf = max(0, ld.count - parents)


def parse_line_items(text: str) -> List[LineItem]:
    """
    Parse pasted Line Item info with columns:
    Module | Line Item | Applies To (';' separated list names, '-' for none)
    | Data Type | Summary Method | Time Periods | Versions
    """
    items: List[LineItem] = []
    for raw_line in text.strip().splitlines():
        if not raw_line.strip():
            continue
        cols = _split_row(raw_line)
        if cols[0].lower() in ("module", "module name"):
            continue  # header row
        if len(cols) < 2:
            continue
        module = cols[0]
        name = cols[1]
        applies_raw = cols[2] if len(cols) > 2 else "-"
        applies_to = [] if applies_raw.strip() in ("-", "", "None") else [
            a.strip() for a in applies_raw.split(";") if a.strip()
        ]
        data_type = cols[3] if len(cols) > 3 and cols[3] else "Number"
        summary = cols[4] if len(cols) > 4 and cols[4] else "None"
        time_periods, time_scale = 0, ""
        raw_time = cols[5].strip() if len(cols) > 5 else ""
        if raw_time.isdigit():
            time_periods = int(raw_time)
        else:
            for sc in PERIODS_PER_YEAR:
                if raw_time.lower().startswith(sc):
                    time_scale = sc
                    break
        try:
            versions = int(cols[6]) if len(cols) > 6 and cols[6] else 1
        except ValueError:
            versions = 1
        items.append(LineItem(
            module=module, name=name, applies_to=applies_to,
            data_type=data_type, summary=summary,
            time_periods=time_periods, time_scale=time_scale, versions=versions,
        ))
    return items


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def is_summarised(summary: str) -> bool:
    return (summary or "none").strip().lower() not in ("", "none")


def periods_for(scale: str, summarised: bool, years: Optional[int] = None) -> int:
    per = PERIODS_PER_YEAR.get(scale)
    if not per:
        return 1
    y = years or CALENDAR_YEARS
    leaf = per * y
    return leaf + y if summarised and scale != "year" else leaf


def dim_members(lists: Dict[str, ListDef], name: str, summarised: bool) -> int:
    ld = lists.get(name)
    if ld is None:
        return 1
    if summarised:
        return ld.count
    return ld.leaf if ld.leaf is not None else ld.count


def line_item_cells(lists: Dict[str, ListDef], li: LineItem, years: Optional[int] = None) -> float:
    """cells = time periods x members of each list x versions (see module docstring)."""
    sm = is_summarised(li.summary)
    cells = 1.0
    if li.time_scale:
        cells *= periods_for(li.time_scale, sm, years)
    elif li.time_periods > 0:
        cells *= li.time_periods
    for ln in li.applies_to:
        cells *= dim_members(lists, ln, sm)
    if li.versions and li.versions > 0:
        cells *= li.versions
    return cells


def compute_workspace(lists: Dict[str, ListDef], line_items: List[LineItem]) -> WorkspaceResult:
    modules: Dict[str, ModuleResult] = {}
    li_results: List[LineItemResult] = []
    total_cells = 0.0
    total_mb = 0.0

    for li in line_items:
        sm = is_summarised(li.summary)
        dim_size = 1
        for ln in li.applies_to:
            dim_size *= dim_members(lists, ln, sm)
        cells = line_item_cells(lists, li)

        bytes_per_cell = BYTES_PER_CELL.get(li.data_type.strip().lower(), 4)
        size_mb = (cells * bytes_per_cell) / 1_000_000

        li_results.append(LineItemResult(
            module=li.module, name=li.name, dimension_size=dim_size,
            cells=cells, size_mb=size_mb,
        ))

        total_cells += cells
        total_mb += size_mb

        mod = modules.get(li.module)
        if mod is None:
            modules[li.module] = ModuleResult(module=li.module, cells=cells, size_mb=size_mb, line_items=1)
        else:
            mod.cells += cells
            mod.size_mb += size_mb
            mod.line_items += 1

    return WorkspaceResult(
        total_cells=total_cells, total_size_mb=total_mb,
        modules=modules, line_item_results=li_results,
    )


# ---------------------------------------------------------------------------
# Change simulators — each returns a NEW (lists, line_items) pair; the
# originals are never mutated so "current" can always be recomputed cleanly.
# ---------------------------------------------------------------------------

def add_new_list(lists, line_items, new_name: str, new_count: int,
                  apply_to_modules: Optional[List[str]] = None):
    """Add a brand-new list, optionally wiring it as an extra dimension
    onto every existing line item in the given modules."""
    new_lists = copy.deepcopy(lists)
    new_items = copy.deepcopy(line_items)
    new_lists[new_name] = ListDef(name=new_name, count=new_count, leaf=new_count)

    if apply_to_modules:
        for li in new_items:
            if li.module in apply_to_modules and new_name not in li.applies_to:
                li.applies_to.append(new_name)
    return new_lists, new_items


def grow_existing_list(lists, line_items, list_name: str, additional_items: int):
    """Increase an existing list's item count (e.g. Admin adds members)."""
    new_lists = copy.deepcopy(lists)
    new_items = copy.deepcopy(line_items)
    if list_name in new_lists:
        target = new_lists[list_name]
        target.leaf = (target.leaf if target.leaf is not None else target.count) + additional_items
        target.count += additional_items          # new items are leaf members
        for ld in new_lists.values():             # lists below it gain them as parent members
            cur, guard = ld.parent_hierarchy, 0
            while cur and guard < 10:
                if cur == list_name:
                    ld.count += additional_items
                    break
                cur = new_lists[cur].parent_hierarchy if cur in new_lists else ""
                guard += 1
    return new_lists, new_items


def add_line_items(lists, line_items, module: str, count: int,
                    applies_to: List[str], data_type: str, summary: str,
                    time_periods: int = 0, versions: int = 1,
                    time_scale: str = "", name_prefix: str = "New Line Item"):
    """Add N new line items to an existing (or new) module."""
    new_lists = copy.deepcopy(lists)
    new_items = copy.deepcopy(line_items)
    for i in range(1, count + 1):
        new_items.append(LineItem(
            module=module, name=f"{name_prefix} {i}", applies_to=list(applies_to),
            data_type=data_type, summary=summary,
            time_periods=time_periods, time_scale=time_scale, versions=versions,
        ))
    return new_lists, new_items


def add_new_module(lists, line_items, module_name: str, line_item_count: int,
                    applies_to: List[str], data_type: str, summary: str,
                    time_periods: int = 0, versions: int = 1, time_scale: str = ""):
    """Add a brand-new module with N line items."""
    return add_line_items(
        lists, line_items, module=module_name, count=line_item_count,
        applies_to=applies_to, data_type=data_type, summary=summary,
        time_periods=time_periods, versions=versions, time_scale=time_scale,
        name_prefix=f"{module_name} Line Item",
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_report(current: WorkspaceResult, projected: WorkspaceResult, change_desc: str) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("ANAPLAN WORKSPACE CAPACITY IMPACT REPORT")
    lines.append("=" * 78)
    lines.append(f"Requested change: {change_desc}")
    lines.append("-" * 78)

    delta_cells = projected.total_cells - current.total_cells
    delta_mb = projected.total_size_mb - current.total_size_mb
    pct = (delta_cells / current.total_cells * 100) if current.total_cells else 0.0

    lines.append(f"{'Metric':<28}{'Current':>15}{'Projected':>17}{'Delta':>15}")
    lines.append(f"{'Total cells':<28}{current.total_cells:>15,.0f}{projected.total_cells:>17,.0f}{delta_cells:>15,.0f}")
    lines.append(f"{'Est. workspace size (MB)':<28}{current.total_size_mb:>15,.2f}{projected.total_size_mb:>17,.2f}{delta_mb:>15,.2f}")
    lines.append(f"Overall change: {pct:+.2f}%")
    lines.append("-" * 78)
    lines.append(f"{'Module':<30}{'Current cells':>15}{'Projected cells':>18}{'Delta %':>12}")
    lines.append("-" * 78)

    all_modules = sorted(set(current.modules) | set(projected.modules))
    for m in all_modules:
        c = current.modules.get(m)
        p = projected.modules.get(m)
        c_cells = c.cells if c else 0.0
        p_cells = p.cells if p else 0.0
        mod_pct = ((p_cells - c_cells) / c_cells * 100) if c_cells else (100.0 if p_cells else 0.0)
        flag = "  <- NEW" if not c and p else ("" if abs(mod_pct) < 0.01 else "")
        lines.append(f"{m:<30}{c_cells:>15,.0f}{p_cells:>18,.0f}{mod_pct:>11.1f}%{flag}")

    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-contained demo
# ---------------------------------------------------------------------------

SAMPLE_LISTS_TEXT = "\t".join(LIST_EXPORT_COLUMNS_DISPLAY := [
    "Name", "Top Level", "Parent Hierarchy", "Selective Access", "Production Data",
    "Managed By", "Has Workflow", "Properties", "Subsets", "Numbered",
    "Display Name Property", "Is Organization", "Category", "Data Tags", "Item Count",
    "Notes", "Referenced in Applies To", "Referenced as Format", "Referenced in Formula",
    "Next item index", "Use Top Level As Default Page", "Delegated",
]) + "\n" + "\n".join([
    "Users\t\t\t\tTRUE\t\tFALSE\t\t\tFALSE\t\tFALSE\t\t\t30\t\t\t\t\t606\tFALSE\tFALSE",
    "For Heat Map - I\t\t\tFALSE\tFALSE\t\tFALSE\t\t\tFALSE\t\tFALSE\t\t\t5\t\t\t'Map Chart -US'.'Heat Map - I'\t\t6\tFALSE\tFALSE",
    "Cost Center\t\t\tFALSE\tTRUE\t\tFALSE\t\t\tFALSE\t\tFALSE\t\t\t180\t\t\t\t\t181\tFALSE\tFALSE",
    "Product\t\t\tFALSE\tTRUE\t\tFALSE\t\t\tFALSE\t\tFALSE\t\t\t1200\t\t\t\t\t1201\tFALSE\tFALSE",
    "Employee\t\t\tFALSE\tTRUE\t\tFALSE\t\t\tFALSE\t\tFALSE\t\t\t950\t\t\t\t\t951\tFALSE\tFALSE",
    "Region\t\tRegion Group\tFALSE\tTRUE\t\tFALSE\t\t\tFALSE\t\tFALSE\t\t\t12\t\t\t\t\t13\tFALSE\tFALSE",
])

SAMPLE_LINE_ITEMS_TEXT = """Module,Line Item,Applies To,Data Type,Summary Method,Time Periods,Versions
Opex Planning,Budget Amount,Cost Center;Region,Number,Sum,12,3
Opex Planning,Actuals Amount,Cost Center;Region,Number,Sum,12,3
Opex Planning,Variance %,Cost Center;Region,Number,Formula,12,3
Revenue Planning,Forecast Units,Product;Region,Number,Sum,12,3
Revenue Planning,Forecast Price,Product;Region,Number,Average,12,3
Workforce Planning,Headcount,Employee;Cost Center,Number,Sum,12,1
Workforce Planning,FTE Ratio,Employee;Cost Center,Number,Ratio,12,1
"""


def _demo():
    lists = parse_lists(SAMPLE_LISTS_TEXT)
    line_items = parse_line_items(SAMPLE_LINE_ITEMS_TEXT)

    current = compute_workspace(lists, line_items)
    print("Parsed", len(lists), "lists and", len(line_items), "line items from sample data.\n")

    print(">>> SCENARIO 1: Admin adds a new List 'Business Unit' (25 members) and")
    print("    wires it into Opex Planning as an extra dimension\n")
    l1, li1 = add_new_list(lists, line_items, new_name="Business Unit", new_count=25,
                            apply_to_modules=["Opex Planning"])
    proj1 = compute_workspace(l1, li1)
    print(format_report(current, proj1,
          "Add List 'Business Unit' (25 items) as a dimension on module 'Opex Planning'"))

    print("\n>>> SCENARIO 2: Admin grows existing list 'Product' by 300 new SKUs\n")
    l2, li2 = grow_existing_list(lists, line_items, list_name="Product", additional_items=300)
    proj2 = compute_workspace(l2, li2)
    print(format_report(current, proj2, "Grow List 'Product' by 300 items (1200 -> 1500)"))

    print("\n>>> SCENARIO 3: Admin adds 4 new Number line items to 'Revenue Planning'")
    print("    dimensioned by Product x Region, monthly, 3 versions, Sum summary\n")
    l3, li3 = add_line_items(lists, line_items, module="Revenue Planning", count=4,
                              applies_to=["Product", "Region"], data_type="Number",
                              summary="Sum", time_scale="month", versions=3)
    proj3 = compute_workspace(l3, li3)
    print(format_report(current, proj3,
          "Add 4 Number line items to module 'Revenue Planning' (Product x Region, monthly, 3 versions)"))

    print("\n>>> SCENARIO 4: Admin adds a brand-new module 'Capex Planning' with")
    print("    6 line items dimensioned by Cost Center x Region, monthly, 3 versions\n")
    l4, li4 = add_new_module(lists, line_items, module_name="Capex Planning", line_item_count=6,
                              applies_to=["Cost Center", "Region"], data_type="Number",
                              summary="Sum", time_scale="month", versions=3)
    proj4 = compute_workspace(l4, li4)
    print(format_report(current, proj4,
          "Add new module 'Capex Planning' with 6 line items (Cost Center x Region, monthly, 3 versions)"))


def _demo_leaf_vs_summary():
    """Reproduces an Anaplan-validated example: adding a line item dimensioned by
    Location x Products on a monthly (3-year) calendar."""
    def mk(name, count, parent="", top=""):
        return ListDef(name=name, count=count, parent_hierarchy=parent, top_level=top)
    lists = {
        "G1 Region": mk("G1 Region", 4, top="All Regions"),
        "G2 Country": mk("G2 Country", 17, parent="G1 Region"),
        "G3 Location": mk("G3 Location", 58, parent="G2 Country"),
        "P1 Product Family": mk("P1 Product Family", 6, top="All Products"),
        "P2 Products": mk("P2 Products", 59, parent="P1 Product Family"),
    }
    compute_leaf_counts(lists)
    print("\n>>> LEAF vs SUMMARY example (Location x Products, monthly, 3-year calendar)")
    print(f"    leaf members: Location={lists['G3 Location'].leaf}, Products={lists['P2 Products'].leaf}")
    for summary in ("None", "Sum"):
        _, items = add_line_items(lists, [], module="REV03 Margin Calculation", count=1,
                                  applies_to=["G3 Location", "P2 Products"], data_type="Number",
                                  summary=summary, time_scale="month")
        print(f"    summary={summary:<4} -> {line_item_cells(lists, items[0]):,.0f} cells")


if __name__ == "__main__":
    _demo()
    _demo_leaf_vs_summary()
