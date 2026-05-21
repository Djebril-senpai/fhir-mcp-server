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

from fhir_mcp_server.filtering.fhirpath_evaluator import evaluate_fhirpath


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


def _filter_entry(
    entry: Dict[str, Any],
    paths: List[str],
    is_search: bool,
) -> Dict[str, Any]:
    if "resource" not in entry or not isinstance(entry["resource"], dict):
        return evaluate_fhirpath(entry, paths)

    resource = entry["resource"]
    search_mode = entry.get("search", {}).get("mode")
    resource_type = resource.get("resourceType", "")

    # e.g. searchset Bundle → entry → collection Bundle → filter its entries too
    if resource_type == "Bundle":
        _, inner_paths = _split_bundle_resource_paths(paths)
        inner_entries = [
            evaluate_fhirpath(inner_entry["resource"], inner_paths)
            if "resource" in inner_entry and isinstance(inner_entry["resource"], dict)
            else inner_entry
            for inner_entry in resource.get("entry", [])
        ]
        result = {"entry": inner_entries}
        if search_mode == "include":
            result["search"] = entry["search"]
        return result

    if not is_search or search_mode == "include":
        always_include = [f"{resource_type}.id", f"{resource_type}.resourceType"]
    elif search_mode == "match":
        always_include = [f"{resource_type}.id"]
    else:
        always_include = []

    result = evaluate_fhirpath(resource, always_include + paths)
    if search_mode == "include":
        result["search"] = entry["search"]
    return result


def _process_one_resource(
    item: Any,
    field_paths: List[str],
    is_search: bool,
) -> Any:
    if not isinstance(item, dict):
        return item

    if item.get("resourceType") == "Bundle":
        bundle_paths, resource_paths = _split_bundle_resource_paths(field_paths)
        result = evaluate_fhirpath(item, bundle_paths) if bundle_paths else {}
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
    if not field_paths:
        return data

    if isinstance(data, list):
        return [_process_one_resource(item, field_paths, is_search) for item in data]

    return _process_one_resource(data, field_paths, is_search)