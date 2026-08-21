# Q1135: enrollment submitted for a different method in RecoveryApi.ts

## Question
submitEnrollMfa branches on method === 'passkey' for the MFA-gated path and takes the other branch otherwise; can an attacker choose the ungated branch to enrol a method without an MFA challenge?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call the submit path with a non-passkey method and observe the gate.
- Invariant to test: All enrollment submissions must pass the same gate in src/client/recovery/RecoveryApi.ts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: submit each method through RecoveryApi.getRecoveryKeyMaterial and assert every path is MFA-gated.
