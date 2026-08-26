# Q5161: replay across time, don or method in httpserver.splitURL

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at the public gateway user HTTP endpoint (POST to the configured user path) and replay it through `splitURL` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `splitURL`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request repetition rate (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `request repetition rate` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
