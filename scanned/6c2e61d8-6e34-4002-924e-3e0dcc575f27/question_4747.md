# Q4747: run input reaches the reported value in eth_keys_controller.formatETHKeyResponse

## Question
Can an authenticated node user holding only the 'view' role supply request data through `formatETHKeyResponse` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter that flows into the value the job reports on-chain rather than being confined to metadata?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `formatETHKeyResponse`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `address path parameter` with crafted pipeline input/meta.
- Invariant to test: externally supplied run input must not determine the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the reported value is independent of caller-supplied input
