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


def extract_fields_by_fhirpath(
    resource: Dict[str, Any],
    field_paths: List[str],
) -> Dict[str, Any]:
    """Evaluates a list of FHIRPath expressions against a resource and returns a dict of only the matched fields.

    Expressions must be prefixed with the resource type (e.g. Patient.name).
    Expressions that match the prefix but yield no value are recorded in _not_matched.
    Expressions that cause evaluation errors are recorded in _errors.
    """

    resource_type = resource.get("resourceType", "")

    result: Dict[str, Any] = {}
    not_matched: List[str] = []
    errors: List[str] = []

    for path in field_paths:
        path_parts = path.split(
            "."
        )  # e.g. "Patient.name.family" → ["Patient", "name", "family"]

        resource_type_prefix = path_parts[0]
        if not resource_type_prefix or not resource_type_prefix[0].isupper():
            not_matched.append(path)
            continue

        if resource_type_prefix != resource_type:
            continue

        try:
            matched = fhirpathpy.evaluate(resource, path)
            if matched:
                if len(path_parts) < 2:
                    not_matched.append(path)
                    continue
                result_key = ".".join(path_parts[1:])
                root_field = path_parts[1].split("(")[0]  # "name.where(...)" → "name"

                # fhirpathpy always returns a list; unwrap to a single value (e.g. "1990-01-01" not ["1990-01-01"]) only if the field is not a list in the original resource
                result[result_key] = (
                    matched
                    if isinstance(resource.get(root_field), list) or len(matched) > 1
                    else matched[0]
                )
            else:
                not_matched.append(path)
        except Exception as e:
            logger.warning("FHIRPath eval failed for expression %r: %s", path, e)
            errors.append(path)

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
