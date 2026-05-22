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

from fhir_mcp_server.filtering import filter_resource_fields


class TestFilterResourceFields:

    PATIENT = {
        "resourceType": "Patient",
        "id": "p1",
        "name": [{"family": "Smith"}],
        "gender": "male",
        "birthDate": "1990-01-01",
    }

    OBSERVATION = {
        "resourceType": "Observation",
        "id": "o1",
        "status": "final",
        "valueQuantity": {"value": 7.2, "unit": "mmol/L"},
    }

    def test_no_field_paths_returns_data_unchanged(self):
        assert filter_resource_fields(self.PATIENT) is self.PATIENT

    def test_non_dict_returned_unchanged(self):
        assert filter_resource_fields("raw-string", ["Patient.name"]) == "raw-string"
        assert filter_resource_fields(42, ["Patient.name"]) == 42

    def test_list_of_resources_each_filtered(self):
        result = filter_resource_fields([self.PATIENT, self.OBSERVATION], ["Patient.name"])
        assert "name" in result[0]
        assert "name" not in result[1]

    def test_single_resource_matched(self):
        result = filter_resource_fields(self.PATIENT, ["Patient.name"])
        assert "name" in result

    # --- Bundle traversal ---

    def test_bundle_entries_filtered(self):
        bundle = {
            "resourceType": "Bundle",
            "id": "b1",
            "entry": [
                {"resource": self.PATIENT},
                {"resource": self.OBSERVATION},
            ],
        }
        result = filter_resource_fields(bundle, ["Patient.name"])
        assert len(result["entry"]) == 2
        assert "name" in result["entry"][0]
        assert "name" not in result["entry"][1]

    def test_bundle_mixed_resource_types_filtered_correctly(self):
        """Each Bundle entry is only filtered by the expression matching its resource type."""
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {"resource": self.PATIENT},
                {"resource": self.OBSERVATION},
            ],
        }
        result = filter_resource_fields(
            bundle, ["Patient.name", "Observation.valueQuantity"]
        )
        assert "name" in result["entry"][0]
        assert "valueQuantity" not in result["entry"][0]
        assert "valueQuantity" in result["entry"][1]
        assert "name" not in result["entry"][1]

    def test_bundle_level_path_included_in_result(self):
        bundle = {
            "resourceType": "Bundle",
            "id": "b1",
            "type": "searchset",
            "entry": [{"resource": self.PATIENT}],
        }
        result = filter_resource_fields(bundle, ["Bundle.type", "Patient.name"])
        assert result["type"] == "searchset"
        assert "name" in result["entry"][0]

    def test_bundle_entry_wrapper_preserves_search(self):
        """search metadata on the entry wrapper must be carried through after filtering."""
        entry = {
            "fullUrl": "http://example.com/Patient/p1",
            "resource": self.PATIENT,
            "search": {"mode": "include"},
        }
        result = filter_resource_fields(entry, ["Patient.name"])
        assert result["search"] == {"mode": "include"}
        assert "name" in result

    def test_bundle_entry_wrapper_drops_full_url(self):
        """fullUrl is intentionally not preserved — only the filtered resource fields and search are kept."""
        entry = {
            "fullUrl": "http://example.com/Patient/p1",
            "resource": self.PATIENT,
        }
        result = filter_resource_fields(entry, ["Patient.name"])
        assert "fullUrl" not in result

    # --- always_include logic (id / resourceType) ---

    def test_read_plain_resource_omits_id_and_resource_type(self):
        """A directly read resource: only the requested fields; caller already knows what they asked for."""
        result = filter_resource_fields(self.PATIENT, ["Patient.name"])
        assert "id" not in result
        assert "resourceType" not in result

    def test_read_bundle_entries_include_id_and_resource_type(self):
        """$everything / collection Bundle entries include id and resourceType — entries can be any resource type."""
        bundle = {
            "resourceType": "Bundle",
            "entry": [{"resource": self.PATIENT}],
        }
        result = filter_resource_fields(bundle, ["Patient.name"])
        assert "id" in result["entry"][0]
        assert "resourceType" in result["entry"][0]

    def test_search_match_entry_has_id_not_resource_type(self):
        """match entries include id for cross-referencing but omit resourceType — caller knows the type from the query."""
        entry = {"resource": self.PATIENT, "search": {"mode": "match"}}
        result = filter_resource_fields(entry, ["Patient.name"], is_search=True)
        assert "id" in result
        assert "resourceType" not in result

    def test_search_include_entry_has_id_and_resource_type(self):
        """_include entries include both because they can be any resource type."""
        entry = {"resource": self.PATIENT, "search": {"mode": "include"}}
        result = filter_resource_fields(entry, ["Patient.name"], is_search=True)
        assert "id" in result
        assert "resourceType" in result
