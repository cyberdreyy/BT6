# Q4171: undecodable responses counted as valid in message_util.ValidatedMessageFromReq

## Question
Does `ValidatedMessageFromReq` at validated conversion between gateway messages, requests and responses count undecodable or error responses toward success, letting any internet client with an arbitrary externally-owned key sending signed gateway requests force a result with fewer honest contributions?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: response fields echoed from the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the mixed-response branch with `response fields echoed from the request`.
- Invariant to test: only successfully decoded, verified responses may count
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test with mixed decodable/undecodable responses
