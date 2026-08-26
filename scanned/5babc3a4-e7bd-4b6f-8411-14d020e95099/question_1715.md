# Q1715: metadata sync race grants access in webapi.Validate

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit at the web-API capability handler config validation and request path from the public gateway endpoint during the metadata refresh handled by `Validate` so authorization is evaluated against empty or stale metadata and defaults to allow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: the request URL, method and headers in the payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `request URL, method and headers in the payload` against the sync tick.
- Invariant to test: authorization must fail closed while metadata is unavailable or stale
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test submitting during a metadata gap and asserting rejection
