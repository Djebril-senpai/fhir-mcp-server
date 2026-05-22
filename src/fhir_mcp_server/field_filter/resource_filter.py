# Copyright (c) 2026, WSO2 LLC. (https://www.wso2.com/) All Rights Reserved.

# WSO2 LLC. licenses this file to you under the Apache License,
# Version 2.0 (the "License"); you may not use this file except
# in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

from typing import Any, Dict, List

from fhir_mcp_server.field_filter.fhirpath_field_extractor import (
    extract_fields_by_fhirpath,
)


def _split_bundle_resource_paths(
    field_paths: List[str],
) -> tuple[List[str], List[str]]:
    """Split field_paths into two lists:

    - bundle_paths: Bundle.* paths applied to the Bundle wrapper only
    - resource_paths: paths applied to each entry resource — includes Bundle.entry.resource.* (prefix stripped) and resource-type paths (e.g. Patient.name)
    """

    bundle_paths = []
    resource_paths = []
    for path in field_paths:
        if path.startswith("Bundle.entry.resource."):
            resource_paths.append(path.removeprefix("Bundle.entry.resource."))
        elif path.startswith("Bundle."):
            bundle_paths.append(path)
        else:
            resource_paths.append(path)
    return bundle_paths, resource_paths


def _always_include_field_paths(
    resource_type: str, is_search: bool, search_mode: str | None
) -> List[str]:
    """Returns the FHIRPath expressions for id and resourceType that must be included alongside the caller's requested fields.

    Direct resource reads: id, and resourceType not included as caller knows it.
    $everything / collection Bundle entries: both — entries can be any resource type, caller needs to distinguish them.
    Search _include entries: both id and resourceType included resources can be any type.
    Search match entries: id only as caller knows the type from the query, but needs id to distinguish the resources.
    """
    if not is_search or search_mode == "include":
        return [f"{resource_type}.id", f"{resource_type}.resourceType"]
    if search_mode == "match":
        return [f"{resource_type}.id"]
    return []


def _filter_nested_bundle(
    resource: Dict[str, Any],
    paths: List[str],
    is_search: bool,
    search_mode: str | None,
) -> Dict[str, Any]:
    """Filter a Bundle that is itself an entry resource (e.g. a collection Bundle inside a searchset).

    Extracts the Bundle's own id/resourceType, then filters each of its entries separately.
    """
    always_include = _always_include_field_paths("Bundle", is_search, search_mode)
    result = extract_fields_by_fhirpath(resource, always_include)
    filtered_entries = []
    for inner_entry in resource.get("entry", []):
        if "resource" in inner_entry and isinstance(inner_entry["resource"], dict):
            inner_resource = inner_entry["resource"]
            inner_resource_type = inner_resource.get("resourceType", "")
            inner_always_include = _always_include_field_paths(
                inner_resource_type, is_search, search_mode
            )
            inner_result = extract_fields_by_fhirpath(
                inner_resource, inner_always_include + paths
            )

            # strip always_include paths from _not_matched — if caller never requested them
            if "_not_matched" in inner_result:
                inner_result["_not_matched"] = [
                    path for path in inner_result["_not_matched"] if path in paths
                ]
                if not inner_result["_not_matched"]:
                    del inner_result["_not_matched"]

            filtered_entries.append(inner_result)
        else:
            filtered_entries.append(inner_entry)
    result["entry"] = filtered_entries
    return result


def _filter_entry(
    entry: Dict[str, Any],
    paths: List[str],
    is_search: bool,
) -> Dict[str, Any]:
    """Filter a single Bundle entry's resource fields and carry through search metadata."""

    if "resource" not in entry or not isinstance(entry["resource"], dict):
        return extract_fields_by_fhirpath(entry, paths)

    resource = entry["resource"]
    search_mode = entry.get("search", {}).get("mode")
    resource_type = resource.get("resourceType", "")

    if resource_type == "Bundle":
        result = _filter_nested_bundle(resource, paths, is_search, search_mode)
    else:
        always_include = _always_include_field_paths(
            resource_type, is_search, search_mode
        )
        result = extract_fields_by_fhirpath(resource, always_include + paths)

    if search_mode == "include":
        result["search"] = entry.get("search")
    return result


def _filter_resource(
    item: Any,
    field_paths: List[str],
    is_search: bool,
) -> Any:
    """Filter a single resource or Bundle, routing Bundle entries through _filter_entry."""

    if not isinstance(item, dict):
        return item

    if item.get("resourceType") == "Bundle":
        bundle_paths, resource_paths = _split_bundle_resource_paths(field_paths)
        result = extract_fields_by_fhirpath(item, bundle_paths) if bundle_paths else {}
        if resource_paths:
            result["entry"] = [
                _filter_entry(entry, resource_paths, is_search)
                for entry in item.get("entry", [])
            ]
        return result

    return _filter_entry(item, field_paths, is_search)


def filter_resource_fields(
    data: Any,
    field_paths: List[str] | None = None,
    is_search: bool = False,
) -> Any:
    """Filter a FHIR resource or list of resources to only the fields matching the given FHIRPath expressions.

    Handles Bundle traversal — expressions are applied to each entry's resource.
    Set is_search=True for searchset Bundles to apply search.mode-based id/resourceType inclusion rules.
    Returns data unchanged if field_paths is empty or None.
    """

    if not field_paths:
        return data

    if isinstance(data, list):
        return [_filter_resource(item, field_paths, is_search) for item in data]

    return _filter_resource(data, field_paths, is_search)
