# Q0805: clearMfa userId is caller supplied in RecoveryApi.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through RecoveryApi.getRecoveryKeyMaterial to drop MFA state that is not theirs?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call RecoveryApi.getRecoveryKeyMaterial with a foreign userId and assert the session's own id is used or the call is refused.
