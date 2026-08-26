# Q0298: replay across time, don or method in gateway.NewGatewayFromConfig

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at ProcessRequest on the public gateway user endpoint and replay it through `NewGatewayFromConfig` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `NewGatewayFromConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: method and donId routing fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `method and donId routing fields` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
