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


def _filter_with_fhirpath(
    resource: Dict[str, Any], field_paths: List[str]
) -> Dict[str, Any]:
    """Apply FHIRPath expressions to a single FHIR resource. Always includes id and resourceType."""

    is_bundle = resource.get("resourceType") == "Bundle"
    resource_type = resource.get("resourceType", "")

    logger.debug(
        "_filter_with_fhirpath: resourceType=%r, paths=%d",
        resource_type,
        len(field_paths),
    )

    result: Dict[str, Any]
    if is_bundle:
        result = {}
    else:
        result: Dict[str, Any] = {
            k: resource[k] for k in ("id", "resourceType") if k in resource
        }  # keep id and resourceType

    unmatched: List[str] = []
    errors: List[str] = []

    for expr in field_paths:
        prefix = expr.split(".")[0]
        if (
            not prefix
            or (prefix[0].isupper() and prefix != resource_type)
            or (is_bundle and prefix[0].islower())
        ):
            logger.debug(
                "  skipping %r (prefix %r does not match resourceType %r)",
                expr,
                prefix,
                resource_type,
            )
            continue
        try:
            matched = fhirpathpy.evaluate(resource, expr)
            if matched:
                key = expr[len(prefix) + 1 :] if prefix == resource_type else expr
                base_field = key.split(".")[0].split("(")[0]
                original = resource.get(base_field)
                value = (
                    matched[0]
                    if len(matched) == 1 and not isinstance(original, list)
                    else matched
                )
                result[key] = value
                logger.debug("  matched %r → key=%r", expr, key)
            else:
                unmatched.append(expr)
        except Exception as e:
            logger.warning("FHIRPath eval failed for expression %r: %s", expr, e)
            errors.append(expr)

    logger.debug(
        "_filter_with_fhirpath done: resourceType=%r, matched=%d, unmatched=%d, errors=%d",
        resource_type,
        len(result)
        - sum(k in result for k in ("id", "resourceType", "_unmatched", "_errors")),
        len(unmatched),
        len(errors),
    )
    if unmatched:
        result["_unmatched"] = unmatched
    if errors:
        result["_errors"] = errors
    return result


def filter_resource_fields(
    data: Any, field_paths: List[str] | None = None, _depth: int = 0
) -> Any:
    """
    If field_paths provided, apply FHIRPath filtering to include only specified fields, always keeping id and resourceType.
    For Bundles, field_paths are applied at the Bundle wrapper level (e.g. Bundle.id) and then recursively to each entry.resource (e.g. Bundle.entry.resource.Patient.name).
    """

    if not field_paths:
        return data

    if _depth > MAX_RECURSION_DEPTH_FOR_FILTERING:
        logger.debug(
            "filter_resource_fields: max recursion depth %d reached, returning data unfiltered",
            _depth,
        )
        return data

    if isinstance(data, list):
        return [filter_resource_fields(item, field_paths, _depth + 1) for item in data]

    if isinstance(data, dict):
        if data.get("resourceType") == "Bundle":
            entry_field_paths = []
            for expr in field_paths:
                if expr.startswith("Bundle.entry.resource."):
                    entry_field_paths.append(
                        expr.removeprefix("Bundle.entry.resource.")
                    )
                elif not expr.startswith("Bundle."):
                    entry_field_paths.append(expr)
            logger.debug(
                "filter_resource_fields: Bundle with %d entries, %d total paths → %d entry paths",
                len(data.get("entry", [])),
                len(field_paths),
                len(entry_field_paths),
            )
            result = _filter_with_fhirpath(data, field_paths)
            if entry_field_paths:
                result["entry"] = [
                    filter_resource_fields(entry, entry_field_paths, _depth + 1)
                    for entry in data.get("entry", [])
                ]
            return result

        if "resource" in data and isinstance(data["resource"], dict):
            logger.debug(
                "filter_resource_fields: unwrapping entry resource (resourceType=%r)",
                data["resource"].get("resourceType", ""),
            )
            result = _filter_with_fhirpath(data["resource"], field_paths)

            if "search" in data:
                result["search"] = data[
                    "search"
                ]  # preserve match/include mode for _include queries
            return result

        return _filter_with_fhirpath(data, field_paths)

    return data


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
