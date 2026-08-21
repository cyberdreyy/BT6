# Q2996: embedded_wallet_config.mode changes the key custody path in logger.ts

## Question
EmbeddedWalletApi branches on config.embedded_wallet_config.mode ('user-controlled-server-wallets-only'); can an attacker influence which branch logger levels NONE/ERROR/WARN/INFO/DEBUG takes so a wallet is created under a different custody model than the app intends?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Serve a config with a flipped mode and observe the create path taken.
- Invariant to test: The custody branch must be authenticated and not flip based on a single fetched field.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: flip the mode field between two calls and assert logger levels NONE/ERROR/WARN/INFO/DEBUG does not silently change custody path for an existing wallet.
