# Q2873: run input reaches the reported value in loop_registry.discoveryHandler

## Question
Can an authenticated node user holding only the 'view' role supply request data through `discoveryHandler` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) that flows into the value the job reports on-chain rather than being confined to metadata?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `discoveryHandler`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the POST /symbol body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `POST /symbol body` with crafted pipeline input/meta.
- Invariant to test: externally supplied run input must not determine the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the reported value is independent of caller-supplied input
