# Q0299: replay across time, don or method in multihandler.NewMultiHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests and replay it through `NewMultiHandler` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `NewMultiHandler`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: the requested method name (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `requested method name` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
