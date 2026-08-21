# Q0585: mfaRequired event carries no operation identity in RecoveryApi.ts

## Question
The 'mfaRequired' event emitted from src/client/recovery/RecoveryApi.ts does not identify which operation triggered it; can an attacker exploit this so the app collects a code for the wrong pending action?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Trigger two operations and inspect the event payload the app receives.
- Invariant to test: MFA prompts must be attributable to the exact operation awaiting them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert the event payload emitted during RecoveryApi.getRecoveryKeyMaterial identifies the pending operation.
