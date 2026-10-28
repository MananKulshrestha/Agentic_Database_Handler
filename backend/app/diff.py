"""Pure snapshot comparison; also used by the API and report job."""

from collections.abc import Iterable


def _key(obj):
    return (obj.schema_name, obj.name, obj.kind)


def _label(key):
    return {"schema": key[0], "name": key[1], "kind": key[2]}


def compare_snapshots(before: Iterable, after: Iterable) -> dict:
    old = {_key(obj): obj for obj in before}
    new = {_key(obj): obj for obj in after}
    added = [{**_label(key), "object_id": new[key].id} for key in sorted(new.keys() - old.keys())]
    removed = [{**_label(key), "object_id": old[key].id, "impact": "potentially_breaking"} for key in sorted(old.keys() - new.keys())]
    changed = []
    for key in sorted(old.keys() & new.keys()):
        left, right = old[key].definition, new[key].definition
        if left == right:
            continue
        old_columns = {col["name"]: col for col in left.get("columns", [])}
        new_columns = {col["name"]: col for col in right.get("columns", [])}
        column_changes = []
        for name in sorted(old_columns.keys() | new_columns.keys()):
            if name not in old_columns:
                column_changes.append({"column": name, "change": "added", "impact": "review" if not new_columns[name].get("nullable", True) else "nonbreaking"})
            elif name not in new_columns:
                column_changes.append({"column": name, "change": "removed", "impact": "potentially_breaking"})
            elif old_columns[name] != new_columns[name]:
                column_changes.append({"column": name, "change": "modified", "before": old_columns[name], "after": new_columns[name], "impact": "review"})
        structural = {}
        for part in ("primary_key", "foreign_keys", "indexes", "unique_constraints", "check_constraints"):
            if left.get(part) != right.get(part):
                structural[part] = {"before": left.get(part), "after": right.get(part)}
        changed.append({**_label(key), "before_object_id": old[key].id, "after_object_id": new[key].id, "columns": column_changes, "structure": structural, "impact": "potentially_breaking" if any(c["impact"] == "potentially_breaking" for c in column_changes) else "review"})
    return {"added": added, "removed": removed, "changed": changed, "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)}}
