# Q5459: error response discloses secret material in workflow_metadata_handler.syncMetadata

## Question
Do error paths in `syncMetadata` at the workflow metadata/authorization lookup consulted for every user trigger request include node responses, partial plaintext or key identifiers that reveal secret material to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `syncMetadata`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: timing relative to metadata sync ticks (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force partial failure with `timing relative to metadata sync ticks`.
- Invariant to test: error paths must not carry node payloads to the user
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting error payloads exclude node data
