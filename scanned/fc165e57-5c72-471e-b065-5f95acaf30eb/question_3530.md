# Q3530: error response discloses secret material in http_handler.HandleNodeMessage

## Question
Do error paths in `HandleNodeMessage` at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) include node responses, partial plaintext or key identifiers that reveal secret material to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `HandleNodeMessage`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the JSON-RPC method and params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force partial failure with `JSON-RPC method and params`.
- Invariant to test: error paths must not carry node payloads to the user
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting error payloads exclude node data
