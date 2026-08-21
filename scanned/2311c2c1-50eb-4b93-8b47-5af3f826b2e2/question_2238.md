# Q2238: mfa error guards accept plain objects in index.ts

## Question
errorIndicatesMfaTimeout/VerificationFailed/MaxMfaRetries duck-type on error.type; can an attacker make throwIfInvalidRecoveryUpgradePath classify a crafted object as an MFA outcome and take the corresponding branch?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Deliver a crafted error object through the reachable error path.
- Invariant to test: MFA outcome classification must rely on authenticated error provenance.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted error objects to each guard reachable from throwIfInvalidRecoveryUpgradePath and assert provenance is required.
