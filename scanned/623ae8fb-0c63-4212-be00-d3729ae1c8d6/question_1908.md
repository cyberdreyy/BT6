# Q1908: recovery key material fetched by address in index.ts

## Question
RecoveryApi.getRecoveryKeyMaterial takes an address path param and chain_type body; can an attacker request material for an address that is not theirs through throwIfInvalidRecoveryUpgradePath?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call the method with another user's wallet address.
- Invariant to test: Recovery material requests must be scoped to wallets owned by the authenticated user.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call throwIfInvalidRecoveryUpgradePath with a foreign address and assert the SDK refuses before the request.
