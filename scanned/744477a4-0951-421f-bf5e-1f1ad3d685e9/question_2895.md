# Q2895: verifyMfa reachable without a pending operation in RecoveryApi.ts

## Question
MfaApi.verifyMfa can be invoked directly; can an attacker call RecoveryApi.getRecoveryKeyMaterial to consume an MFA code outside any operation, leaving a satisfied MFA state that a later operation reuses?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call verifyMfa alone, then immediately start a signing operation.
- Invariant to test: An MFA verification must be consumed by the operation that required it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call RecoveryApi.getRecoveryKeyMaterial then a signature and assert the signature still requires its own MFA round.
