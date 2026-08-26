# Q2235: method routing selects a weaker handler in webapi.Validate

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name a method at the web-API capability handler config validation and request path from the public gateway endpoint that `Validate` routes to a handler with weaker authorization while the payload targets a privileged capability?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: workflow identifiers in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `workflow identifiers in the request` with a mismatched method/payload pair.
- Invariant to test: method routing and payload authorization must be consistent
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test over method/payload mismatches
