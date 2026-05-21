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

import logging
import fhirpathpy

from typing import Any, Dict, List

logger: logging.Logger = logging.getLogger(__name__)


def evaluate_fhirpath(
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
                key = expr.removeprefix(f"{resource_type}.")
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
