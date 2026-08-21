# Q0361: four attempts amplify code guessing in MfaPromises.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use MfaPromises.rootPromise to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/client/MfaPromises.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run MfaPromises.rootPromise repeatedly and assert the total submissions per issued code stay within the budget.
