# Q3448: recovery of a wallet the user does not own in index.ts

## Question
_load recovers based on the passed entropyId and verifier; can an attacker pass an entropyId for another user's wallet through throwIfInvalidRecoveryUpgradePath and trigger a recovery attempt against it?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call the provider path with a foreign entropyId.
- Invariant to test: Entropy identifiers must be verified against the authenticated user's linked accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign entropyId to throwIfInvalidRecoveryUpgradePath and assert it is rejected.
