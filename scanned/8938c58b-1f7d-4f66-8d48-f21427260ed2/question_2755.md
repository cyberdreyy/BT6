# Q2755: path/URL split confusion in message.Sign

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request path at the signed gateway message envelope submitted to the public user endpoint that `Sign` splits differently from the routing layer, reaching a handler or DON that was not authorized?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Sign`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: every MessageBody field (sender, method, donId, messageId, payload) (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `every MessageBody field (sender, method, donId, messageId, payload)` with extra segments, encoded slashes or empty segments.
- Invariant to test: splitting and routing must agree on the same canonical path
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over splitURL with hostile paths
