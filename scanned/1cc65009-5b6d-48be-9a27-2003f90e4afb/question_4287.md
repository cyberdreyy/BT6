# Q4287: method routing selects a weaker handler in workflow_metadata_handler.Authorize

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name a method at the workflow metadata/authorization lookup consulted for every user trigger request that `Authorize` routes to a handler with weaker authorization while the payload targets a privileged capability?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `Authorize`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: workflow owner/name/tag claimed in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `workflow owner/name/tag claimed in the request` with a mismatched method/payload pair.
- Invariant to test: method routing and payload authorization must be consistent
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test over method/payload mismatches
