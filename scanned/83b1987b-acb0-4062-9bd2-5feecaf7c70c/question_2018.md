# Q2018: icloud configuration drives recovery choice in index.ts

## Question
RecoveryICloudApi.getICloudConfiguration returns configuration consumed as trusted; can an attacker influence the returned configuration so throwIfInvalidRecoveryUpgradePath performs recovery against an attacker-chosen record?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Return a configuration naming a foreign record name and observe the recovery attempt.
- Invariant to test: Recovery targets must be bound to the authenticated user's own records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign record configuration and assert throwIfInvalidRecoveryUpgradePath refuses to use it.
