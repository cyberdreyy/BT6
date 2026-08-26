# Q3350: request id mismatch tolerated in message_util.ValidatedMessageFromReq

## Question
Does `ValidatedMessageFromReq` at validated conversion between gateway messages, requests and responses tolerate a mismatch between the id inside a signed payload and the id of the request being answered, letting any internet client with an arbitrary externally-owned key sending signed gateway requests splice a response from another request?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: the message body fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `message body fields` whose ids differ.
- Invariant to test: the signed id must equal the served request id
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting mismatched ids are rejected
