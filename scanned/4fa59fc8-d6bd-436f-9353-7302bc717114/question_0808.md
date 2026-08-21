# Q0808: clearMfa userId is caller supplied in index.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through throwIfInvalidRecoveryUpgradePath to drop MFA state that is not theirs?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call throwIfInvalidRecoveryUpgradePath with a foreign userId and assert the session's own id is used or the call is refused.
