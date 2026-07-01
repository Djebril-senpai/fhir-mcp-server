import json
from urllib.request import urlopen, Request

FHIR_BASE = "https://hapi.fhir.org/baseR4"
METADATA_URL = FHIR_BASE.rstrip('/') + '/metadata?_format=json'

def trim_resource_capabilities(capabilities):
    trimmed = []
    for capability in capabilities:
        if isinstance(capability, dict):
            entry = {}
            if 'name' in capability:
                entry['name'] = capability.get('name')
            if 'documentation' in capability:
                entry['documentation'] = capability.get('documentation')
            if entry:
                trimmed.append(entry)
    return trimmed

req = Request(METADATA_URL, headers={'Accept': 'application/fhir+json'})
with urlopen(req, timeout=30) as resp:
    data = json.load(resp)

patient_entry = None
for rest in data.get('rest', []):
    for resource in rest.get('resource', []):
        if resource.get('type') == 'Patient':
            patient_entry = resource
            break
    if patient_entry:
        break

if not patient_entry:
    print(json.dumps({"error": "Patient not found in CapabilityStatement"}, indent=2))
else:
    out = {
        'type': patient_entry.get('type'),
        'searchParam': trim_resource_capabilities(patient_entry.get('searchParam', [])),
        'operation': trim_resource_capabilities(patient_entry.get('operation', [])),
        'interaction': patient_entry.get('interaction', []),
        'searchInclude': patient_entry.get('searchInclude', []),
        'searchRevInclude': patient_entry.get('searchRevInclude', []),
    }
    print(json.dumps(out, indent=2))
