# Q5164: replay across time, don or method in message.ExtractSigner

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at the signed gateway message envelope submitted to the public user endpoint and replay it through `ExtractSigner` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `ExtractSigner`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: every MessageBody field (sender, method, donId, messageId, payload) (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `every MessageBody field (sender, method, donId, messageId, payload)` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
