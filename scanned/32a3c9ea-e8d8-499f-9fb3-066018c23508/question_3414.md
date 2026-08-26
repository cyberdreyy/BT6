# Q3414: secret ownership check on the wrong field in message_util.ValidatedMessageFromReq

## Question
Does the ownership check for a vault secret in `ValidatedMessageFromReq` at validated conversion between gateway messages, requests and responses use a request field rather than the recovered signer, letting any internet client with an arbitrary externally-owned key sending signed gateway requests read or overwrite another owner's secret?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: response fields echoed from the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `response fields echoed from the request` naming the victim's owner/namespace.
- Invariant to test: secret access must be authorized against the recovered signer only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test reading a foreign owner's secret
