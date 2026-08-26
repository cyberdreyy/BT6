# Q4044: first-to-quorum accepts attacker-shaped result in callback.Wait

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request at the callback used to return a DON response to the originating gateway user so the first aggregator to reach quorum in `Wait` returns a result derived from attacker-controlled input rather than the intended workflow output?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `Wait`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: timing of late responses (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing of late responses` designed to satisfy the weaker aggregator first.
- Invariant to test: all aggregators must apply identical verification before producing a user response
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing verification across aggregators
