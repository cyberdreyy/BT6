# Q2999: embedded_wallet_config.mode changes the key custody path in toSearchParams.ts

## Question
EmbeddedWalletApi branches on config.embedded_wallet_config.mode ('user-controlled-server-wallets-only'); can an attacker influence which branch toSearchParams (skips null/undefined takes so a wallet is created under a different custody model than the app intends?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Serve a config with a flipped mode and observe the create path taken.
- Invariant to test: The custody branch must be authenticated and not flip based on a single fetched field.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: flip the mode field between two calls and assert toSearchParams (skips null/undefined does not silently change custody path for an existing wallet.
