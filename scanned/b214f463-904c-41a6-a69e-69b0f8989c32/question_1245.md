# Q1245: init and submit enrollment not bound in RecoveryApi.ts

## Question
initEnrollMfa and submitEnrollMfa are separate calls with no client-side correlation; can an attacker interleave two enrollments so the code from one is submitted against the other?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Start two enrollments and cross the submissions.
- Invariant to test: Enrollment submissions must be bound to the initialization that produced them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: cross two enrollment flows through RecoveryApi.getRecoveryKeyMaterial and assert the mismatch is rejected.
