# Q3910: revoked workflow still executable in workflow_metadata_handler.Authorize

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `Authorize` at the workflow metadata/authorization lookup consulted for every user trigger request until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `Authorize`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: workflow owner/name/tag claimed in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `workflow owner/name/tag claimed in the request` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
