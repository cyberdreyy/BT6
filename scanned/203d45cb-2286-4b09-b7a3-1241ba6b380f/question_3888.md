# Q3888: recovery access token reused across providers in index.ts

## Question
google-drive and icloud recovery both accept recoveryAccessToken; can an attacker present a token from one provider in the other's branch through throwIfInvalidRecoveryUpgradePath?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call recovery with a mismatched provider/token pair.
- Invariant to test: Recovery tokens must be validated against the provider the wallet is bound to.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross provider and token in throwIfInvalidRecoveryUpgradePath and assert rejection.
