# Q0553: cached response served to a different requester in message_util.ValidatedMessageFromResp

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests obtain a cached response produced for another user because the cache key computed near `ValidatedMessageFromResp` at validated conversion between gateway messages, requests and responses omits the sender or authorization context?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromResp`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: the message body fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Repeat `message body fields` with the victim's request fields.
- Invariant to test: cache keys must include the authenticated sender and authorization inputs
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting cache isolation across senders
