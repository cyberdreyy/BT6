# Q2751: path/URL split confusion in httpserver.NewHTTPServer

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request path at the public gateway user HTTP endpoint (POST to the configured user path) that `NewHTTPServer` splits differently from the routing layer, reaching a handler or DON that was not authorized?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `NewHTTPServer`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: the full request line, path and Origin header (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `full request line, path and Origin header` with extra segments, encoded slashes or empty segments.
- Invariant to test: splitting and routing must agree on the same canonical path
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over splitURL with hostile paths
