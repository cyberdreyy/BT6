# Q3008: access token fetched before every mfa call in index.ts

## Question
MfaApi.getAccessTokenInternal resolves a token per call; can an attacker swap the active session between the token fetch and the proxy call in throwIfInvalidRecoveryUpgradePath so MFA is evaluated against a different identity?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Switch users between the two awaits.
- Invariant to test: MFA operations must pin one identity for their whole duration.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: switch identity mid-call in throwIfInvalidRecoveryUpgradePath and assert the operation aborts.
