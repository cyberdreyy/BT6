# Q0058: signature not covering all authorization fields in message.Validate

## Question
Does the signature validated on the path through `Validate` at the signed gateway message envelope submitted to the public user endpoint cover every field used for authorization (sender, method, donId, receiver, payload), or can any internet client with an arbitrary externally-owned key sending signed gateway requests mutate an uncovered field after signing?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Validate`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: every MessageBody field (sender, method, donId, messageId, payload) (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Sign one message, then alter `every MessageBody field (sender, method, donId, messageId, payload)` before sending.
- Invariant to test: the signed digest must commit to every field later used for routing or authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test mutating each field of a signed message and asserting rejection
