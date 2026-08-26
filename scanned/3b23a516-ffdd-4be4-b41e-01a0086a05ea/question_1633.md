# Q1633: timestamp/nonce window too wide in multihandler.NewMultiHandler

## Question
Does the freshness check near `NewMultiHandler` at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests accept a wide or unbounded window, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay captured requests long after capture?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `NewMultiHandler`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: legacy vs JSON-RPC envelope choice (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `legacy vs JSON-RPC envelope choice` with old/future timestamps.
- Invariant to test: freshness must be bounded and monotonic per sender
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at the window boundaries
