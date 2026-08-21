# Q0581: mfaRequired event carries no operation identity in MfaPromises.ts

## Question
The 'mfaRequired' event emitted from src/client/MfaPromises.ts does not identify which operation triggered it; can an attacker exploit this so the app collects a code for the wrong pending action?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Trigger two operations and inspect the event payload the app receives.
- Invariant to test: MFA prompts must be attributable to the exact operation awaiting them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert the event payload emitted during MfaPromises.rootPromise identifies the pending operation.
