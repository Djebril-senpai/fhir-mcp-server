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

from collections.abc import Mapping
from typing import Any, Dict, List
import logging

from fhir_mcp_server.field_filter.fhirpath_field_extractor import (
    extract_fields_by_fhirpath,
)

logger: logging.Logger = logging.getLogger(__name__)


def _split_bundle_resource_paths(
    field_paths: List[str],
) -> tuple[List[str], List[str]]:
    """Split field_paths into two lists:

    - bundle_wrapper_paths: Bundle.* paths applied to the Bundle wrapper only
    - entry_resource_paths: paths applied to each entry resource — includes Bundle.entry.resource.* (prefix stripped) and resource-type paths (e.g. Patient.name)
    """

    bundle_wrapper_paths = []
    entry_resource_paths = []

    for path in field_paths:
        if path.startswith("Bundle.entry.resource."):
            # Paths like "Bundle.entry.resource.name" become "name" for entry resources
            entry_resource_paths.append(path.removeprefix("Bundle.entry.resource."))
        elif path.startswith("Bundle."):
            # Paths like "Bundle.type" or "Bundle.id" apply to the Bundle wrapper
            bundle_wrapper_paths.append(path)
        else:
            # Resource paths like "Patient.name" apply to entry resources
            entry_resource_paths.append(path)

    return bundle_wrapper_paths, entry_resource_paths


def _preserve_required_fields(original: Mapping[str, Any], filtered: Dict[str, Any]) -> None:
    """Always include id and resourceType in the filtered resource."""
    fields_to_preserve = ["id", "resourceType"]
    for field in fields_to_preserve:
        if field in original and field not in filtered:
            filtered[field] = original[field]


def _filter_nested_bundle(
    resource: Mapping[str, Any],
    paths: List[str],
) -> Dict[str, Any]:
    """
    Filter a Bundle that is itself an entry resource 
    (e.g. a collection Bundle inside a searchset).

    Extracts the Bundle's own id/resourceType,
    then filters each of its entries separately.
    """

    result: Dict[str, Any] = {}
    _preserve_required_fields(resource, result)
    filtered_entries = []

    for inner_entry in resource.get("entry", []):
        if (
            isinstance(inner_entry, Mapping)
            and "resource" in inner_entry
            and isinstance(inner_entry["resource"], Mapping)
        ):
            inner_resource = inner_entry["resource"]
            inner_result = extract_fields_by_fhirpath(inner_resource, paths)
            _preserve_required_fields(inner_resource, inner_result)

            filtered_entries.append(inner_result)
        else:
            filtered_entries.append(inner_entry)
    result["entry"] = filtered_entries
    return result


def _filter_entry(
    entry: Mapping[str, Any],
    paths: List[str],
) -> Dict[str, Any]:
    """Filter a single Bundle entry's resource fields and carry through search metadata."""

    logger.debug(
        "Filtering entry with search metadata %s and paths %s",
        entry.get("search"),
        paths,
    )

    resource = entry.get("resource")

    if isinstance(resource, Mapping):
        resource_type = resource.get("resourceType", "")

        if resource_type == "Bundle":
            result = _filter_nested_bundle(resource, paths)

        else:
            result = extract_fields_by_fhirpath(resource, paths)
            _preserve_required_fields(resource, result)

        if entry.get("search", {}).get("mode") == "include":
            result["search"] = entry.get("search")
    else:
        result = dict(entry)

    return result


def filter_resource_fields(
    data: Any,
    field_paths: List[str] | None = None,
) -> Any:
    """
    Filter a FHIR resource or list of resources to only the
    fields matching the given FHIRPath expressions.

    Handles Bundle traversal — expressions are applied to each entry's resource.
    Returns data unchanged if field_paths is empty or None.
    """

    logger.debug(
        "Filtering resource fields with field paths: %s",
        field_paths,
    )
    if not field_paths:
        return data

    if not isinstance(data, Mapping):
        return data

    # Bundle handle seperately
    if data.get("resourceType") == "Bundle":
        bundle_wrapper_paths, resource_paths = _split_bundle_resource_paths(field_paths)

        result = (
            extract_fields_by_fhirpath(data, bundle_wrapper_paths)
            if bundle_wrapper_paths
            else dict(data)
        )

        if resource_paths:
            result["entry"] = [
                (
                    _filter_entry(entry, resource_paths)
                    if isinstance(entry, Mapping)
                    else entry
                )
                for entry in data.get("entry", [])
            ]
        return result

    # None Bundle resource
    elif "resourceType" in data:
        return extract_fields_by_fhirpath(data, field_paths)

    else:
        return data
