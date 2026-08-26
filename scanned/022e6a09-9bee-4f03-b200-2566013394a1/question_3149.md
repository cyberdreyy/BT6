# Q3149: duplicate response overwrites the result in workflow_metadata_handler.Authorize

## Question
Can a second response accepted by `Authorize` at the workflow metadata/authorization lookup consulted for every user trigger request overwrite an already-delivered result so any internet client with an arbitrary externally-owned key sending signed gateway requests changes what a workflow consumes?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `Authorize`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: workflow owner/name/tag claimed in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force duplicates via `workflow owner/name/tag claimed in the request`.
- Invariant to test: response processing must be single-shot per request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test sending duplicate responses and asserting the first wins
