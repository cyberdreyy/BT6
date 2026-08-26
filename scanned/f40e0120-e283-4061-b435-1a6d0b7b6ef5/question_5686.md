# Q5686: retry amplification per request in workflow_metadata_handler.syncMetadata

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests make one accepted request at the workflow metadata/authorization lookup consulted for every user trigger request cause repeated node work through the retry logic near `syncMetadata`, multiplying DON execution per unit of entitlement?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `syncMetadata`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: workflow owner/name/tag claimed in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `workflow owner/name/tag claimed in the request` that never reaches a terminal state.
- Invariant to test: retries must be bounded per request and counted against the caller's quota
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test counting node messages produced by one user request
