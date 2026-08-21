# Q3228: set-recovery runs after _load succeeded in index.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so throwIfInvalidRecoveryUpgradePath rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of throwIfInvalidRecoveryUpgradePath and assert the operation aborts.
