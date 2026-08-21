# Q3668: enrollment success not verified against refresh in index.ts

## Question
submitEnrollMfa returns the proxy result and then refreshes; can an attacker make throwIfInvalidRecoveryUpgradePath report a successful enrollment that the server never recorded?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Return a success from the iframe path while the refresh shows no methods.
- Invariant to test: Reported enrollment success must be confirmed by the refreshed user state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return success with an empty mfa_methods refresh and assert throwIfInvalidRecoveryUpgradePath reports failure.
