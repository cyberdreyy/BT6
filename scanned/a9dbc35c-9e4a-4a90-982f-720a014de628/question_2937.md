# Q2937: resume/callback path unauthenticated or unbound in loop_registry.discoveryHandler

## Question
Can an authenticated node user holding only the 'view' role resume or complete a pending run through `discoveryHandler` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `discoveryHandler`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: arbitrary sub-paths under the plugin route (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `arbitrary sub-paths under the plugin route` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
