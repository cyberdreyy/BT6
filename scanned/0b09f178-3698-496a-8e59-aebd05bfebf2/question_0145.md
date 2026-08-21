# Q0145: mfaAlwaysRequired only on three operations in RecoveryApi.ts

## Question
Only verifyMfa, unenrollMfa and unlinkPasskey are invoked with mfaAlwaysRequired; can an attacker reach a comparable privileged operation in src/client/recovery/RecoveryApi.ts that skips the always-on gate?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Enumerate the operations routed through invokeWithMfa and compare their flags.
- Invariant to test: Every operation that changes MFA state or produces a signature must be gated identically.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert each privileged operation reachable from RecoveryApi.getRecoveryKeyMaterial sets mfaAlwaysRequired.
