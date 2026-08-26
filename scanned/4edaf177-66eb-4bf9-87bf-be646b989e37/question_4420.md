# Q4420: expired entry cleanup races delivery in message_util.ValidatedMessageFromReq

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at validated conversion between gateway messages, requests and responses so cleanup in `ValidatedMessageFromReq` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: encoding variants of identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `encoding variants of identifiers` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
