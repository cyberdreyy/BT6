# Q4710: length/format validation gap in workflow_metadata_handler.syncMetadata

## Question
Does the field validation in `syncMetadata` at the workflow metadata/authorization lookup consulted for every user trigger request accept an over-long, truncated or non-hex identifier that later code slices or parses unchecked, letting any internet client with an arbitrary externally-owned key sending signed gateway requests address a different workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `syncMetadata`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: authorization key material presented (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `authorization key material presented` at and beyond the documented length bounds.
- Invariant to test: every identifier must be length- and charset-validated before use
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at length boundaries for each identifier field
