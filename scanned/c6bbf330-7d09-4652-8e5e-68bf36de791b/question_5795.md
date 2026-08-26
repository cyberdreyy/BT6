# Q5795: revoked workflow still executable in handler.handleWebAPITriggerMessage

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `handleWebAPITriggerMessage` at web-API trigger and outgoing-request handling on the public gateway user endpoint until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `handleWebAPITriggerMessage`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: target URL/headers of the outgoing HTTP request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `target URL/headers of the outgoing HTTP request` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
