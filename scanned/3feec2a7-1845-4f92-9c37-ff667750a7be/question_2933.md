# Q2933: resume/callback path unauthenticated or unbound in eth_keys_controller.createETHKeyResource

## Question
Can an authenticated node user holding only the 'view' role resume or complete a pending run through `createETHKeyResource` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `createETHKeyResource`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: maxGasPriceWei value (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `maxGasPriceWei value` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
