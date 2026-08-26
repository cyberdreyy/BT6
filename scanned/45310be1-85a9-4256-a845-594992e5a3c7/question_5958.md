# Q5958: validation happens after dispatch in gateway.ProcessRequest

## Question
Does `ProcessRequest` at ProcessRequest on the public gateway user endpoint dispatch the request to the DON before validation completes, so any internet client with an arbitrary externally-owned key sending signed gateway requests's invalid request still consumes DON work or reaches capability code?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `ProcessRequest`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: request repetition and concurrency (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request repetition and concurrency` that fails late in validation.
- Invariant to test: no dispatch may precede complete validation
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test asserting no node message is sent for invalid requests
