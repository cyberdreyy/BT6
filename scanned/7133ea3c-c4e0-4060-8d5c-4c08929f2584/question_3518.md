# Q3518: signature not covering all authorization fields in httpserver.handleHealthCheck

## Question
Does the signature validated on the path through `handleHealthCheck` at the public gateway user HTTP endpoint (POST to the configured user path) cover every field used for authorization (sender, method, donId, receiver, payload), or can any internet client with an arbitrary externally-owned key sending signed gateway requests mutate an uncovered field after signing?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: the full request line, path and Origin header (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Sign one message, then alter `full request line, path and Origin header` before sending.
- Invariant to test: the signed digest must commit to every field later used for routing or authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test mutating each field of a signed message and asserting rejection
