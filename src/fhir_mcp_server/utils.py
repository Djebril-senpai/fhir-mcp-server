# Copyright (c) 2025, WSO2 LLC. (https://www.wso2.com/) All Rights Reserved.

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

import aiohttp
import logging
import fhirpathpy

from fhir_mcp_server.oauth import ServerConfigs

from typing import Any, Dict, List, Optional
from fhirpy import AsyncFHIRClient
from mcp.shared._httpx_utils import create_mcp_http_client

logger: logging.Logger = logging.getLogger(__name__)

MAX_RECURSION_DEPTH_FOR_FILTERING = 10


async def create_async_fhir_client(
    config: ServerConfigs,
    access_token: str | None = None,
    extra_headers: dict | None = None,
) -> AsyncFHIRClient:
    """Create a FHIR AsyncClient with defaults."""

    client_kwargs: Dict = {
        "url": config.server_base_url,
        "aiohttp_config": {
            "timeout": aiohttp.ClientTimeout(total=config.mcp_request_timeout),
        },
        "extra_headers": extra_headers,
    }
    if access_token:
        client_kwargs["authorization"] = f"Bearer {access_token}"

    return AsyncFHIRClient(**client_kwargs)


async def get_bundle_entries(bundle: Dict[str, Any]) -> Dict[str, Any]:
    if bundle and "entry" in bundle and isinstance(bundle["entry"], list):
        logger.debug(f"found {len(bundle['entry'])} entries for type '{type}'")
        return {
            "entry": [
                entry.get("resource")
                for entry in bundle["entry"]
                if "resource" in entry
            ]
        }
    return bundle


def trim_resource_capabilities(
    capabilities: List[Dict[str, Any]],
) -> List[Dict[str, Optional[str]]]:
    logger.debug(
        f"trim_resource_capabilities called with {len(capabilities)} capabilities."
    )
    trimmed = [
        {
            "name": capability.get("name"),
            "documentation": capability.get("documentation"),
        }
        for capability in capabilities
        if "name" in capability or "documentation" in capability
    ]
    logger.debug(
        f"trim_resource_capabilities returning {len(trimmed)} trimmed capabilities."
    )
    return trimmed


async def get_operation_outcome_exception() -> dict:
    return await get_operation_outcome(
        code="exception", diagnostics="An unexpected internal error has occurred."
    )


async def get_operation_outcome_required_error(element: str = "") -> dict:
    return await get_operation_outcome(
        code="required", diagnostics=f"A required element {element} is missing."
    )


async def get_operation_outcome(
    code: str, diagnostics: str, severity: str = "error"
) -> dict:
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ],
    }


async def get_capability_statement(metadata_url: str) -> Dict[str, Any]:
    """
    Discover CapabilityStatement from server's metadata endpoint.
    """
    try:
        logger.debug(f"Fetching CapabilityStatement from {metadata_url}")
        async with create_mcp_http_client() as client:
            response = await client.get(url=metadata_url, headers=get_default_headers())
            response.raise_for_status()
            metadata_json = response.json()
            logger.debug(f"OAuth metadata discovered: {metadata_json}")
            return metadata_json
    except Exception as ex:
        logger.exception(
            "Unable to invoke the FHIR metadata endpoint. Caused by, ", exc_info=ex
        )
        raise ValueError("Unable to fetch FHIR metadata")


def _split_bundle_resource_paths(
    field_paths: List[str],
) -> tuple[List[str], List[str]]:
    """Split field_paths into two buckets:

    - bundle_paths: Bundle.* paths applied to the Bundle wrapper only
    - resource_paths: paths applied to each entry resource — includes Bundle.entry.resource.* (prefix stripped) and resource-type paths (e.g. Patient.name)
    """
    bundle_paths = []
    resource_paths = []
    for p in field_paths:
        if p.startswith("Bundle.entry.resource."):
            resource_paths.append(p.removeprefix("Bundle.entry.resource."))
        elif p.startswith("Bundle."):
            bundle_paths.append(p)
        else:
            resource_paths.append(p)
    return bundle_paths, resource_paths


def _filter_with_fhirpath(
    resource: Dict[str, Any],
    field_paths: List[str],
) -> Dict[str, Any]:
    """Apply FHIRPath expressions to a single FHIR resource."""

    resource_type = resource.get("resourceType", "")

    result: Dict[str, Any] = {}
    not_matched: List[str] = []
    errors: List[str] = []

    for expr in field_paths:
        prefix = expr.split(".")[0]
        if not prefix or not prefix[0].isupper():
            not_matched.append(expr)
            continue
        if prefix != resource_type:
            continue
        try:
            matched = fhirpathpy.evaluate(resource, expr)
            if matched:
                key = (
                    expr.removeprefix(f"{resource_type}.")
                    if prefix == resource_type
                    else expr
                )
                top_level_field = key.split(".")[0].split("(")[0]
                is_array = isinstance(resource.get(top_level_field), list)
                result[key] = matched if len(matched) > 1 or is_array else matched[0]
            else:
                not_matched.append(expr)
        except Exception as e:
            logger.warning("FHIRPath eval failed for expression %r: %s", expr, e)
            errors.append(expr)

    logger.debug(
        "%s: matched %s, skipped %s",
        resource_type or "unknown",
        list(result),
        not_matched + errors,
    )
    if not_matched:
        result["_not_matched"] = not_matched
    if errors:
        result["_errors"] = errors
    return result


def filter_resource_fields(
    data: Any,
    field_paths: List[str] | None = None,
    _depth: int = 0,
    is_search: bool = False,
) -> Any:
    """
    If field_paths provided, apply FHIRPath filtering to include only specified fields.
    For Bundles, field_paths are applied at the Bundle wrapper level and recursively to each entry resource.
    """

    if not field_paths:
        return data

    if _depth >= MAX_RECURSION_DEPTH_FOR_FILTERING:
        logger.debug(
            "filter_resource_fields: max recursion depth %d reached, returning data unfiltered",
            _depth,
        )
        return data

    if isinstance(data, list):
        return [
            filter_resource_fields(item, field_paths, _depth + 1, is_search)
            for item in data
        ]

    if not isinstance(data, dict):
        return data

    resource_type = data.get("resourceType", "")

    if resource_type == "Bundle":
        entries = data.get("entry", [])

        bundle_paths, resource_paths = _split_bundle_resource_paths(field_paths)

        result = _filter_with_fhirpath(data, bundle_paths) if bundle_paths else {}

        if resource_paths:
            result["entry"] = [
                filter_resource_fields(entry, resource_paths, _depth + 1, is_search)
                for entry in entries
            ]
        return result

    # Handle individual Bundle entry resources.
    if "resource" in data and isinstance(data["resource"], dict):
        logger.debug("Hit this line")
        resource = data["resource"]
        search_mode = data.get("search", {}).get("mode")
        resource_type = resource.get("resourceType", "")

        always_include_fields = []

        # _include search mode matches and $operation results are mixed resource types
        if not is_search or search_mode == "include":
            always_include_fields = [
                f"{resource_type}.id",
                f"{resource_type}.resourceType",
            ]
        elif search_mode == "match":
            always_include_fields = [f"{resource_type}.id"]
        result = _filter_with_fhirpath(resource, always_include_fields + field_paths)

        if search_mode == "include":
            result["search"] = data["search"]  # copy search mode from the bundle entry

        return result

    # single resource read
    return _filter_with_fhirpath(data, field_paths)


def get_default_headers() -> Dict[str, str]:
    return {"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"}


def build_user_profile(resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build user profile dictionary from FHIR resource.

    Args:
        resource: The FHIR resource dictionary of the user.

    Returns:
        Dict containing only mandatory user fields
    """

    # Define fields to extract from the resource
    fields_to_extract = [
        "id",
        "resourceType",
        "name",
        "gender",
        "birthDate",
        "telecom",
        "address",
    ]

    profile: Dict[str, Any] = {}
    # Add fields only if they exist and have values
    for field in fields_to_extract:
        value = resource.get(field)
        if value is not None:
            profile[field] = value

    return profile
