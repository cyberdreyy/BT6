# Q2889: cached response served to a different requester in handler.Callback

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests obtain a cached response produced for another user because the cache key computed near `Callback` at the gateway handler interface boundary every public user request passes through omits the sender or authorization context?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `Callback`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: request repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Repeat `request repetition` with the victim's request fields.
- Invariant to test: cache keys must include the authenticated sender and authorization inputs
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting cache isolation across senders
