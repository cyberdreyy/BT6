# Q1688: recovery secret override accepted from caller in index.ts

## Question
setRecovery accepts recoverySecretOverride, iCloudRecordNameOverride, recoveryKey and recoveryAccessToken from the caller; can an attacker pass their own material through throwIfInvalidRecoveryUpgradePath so the victim's wallet becomes recoverable by them?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call the recovery path with attacker-held material for a wallet the attacker can reach.
- Invariant to test: Recovery material accepted by src/embedded/utils/index.ts must be provably held by the wallet's owner.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call throwIfInvalidRecoveryUpgradePath with attacker-supplied override material and assert an MFA/re-auth gate blocks it.
