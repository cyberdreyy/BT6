# Q2757: path/URL split confusion in gateway.setupFromNewConfig

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request path at ProcessRequest on the public gateway user endpoint that `setupFromNewConfig` splits differently from the routing layer, reaching a handler or DON that was not authorized?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `setupFromNewConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: method and donId routing fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method and donId routing fields` with extra segments, encoded slashes or empty segments.
- Invariant to test: splitting and routing must agree on the same canonical path
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over splitURL with hostile paths
