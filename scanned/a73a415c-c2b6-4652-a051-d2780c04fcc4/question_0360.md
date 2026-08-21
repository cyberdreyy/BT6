# Q0360: four attempts amplify code guessing in withMfa.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use withMfa retry loop (4 attempts to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/embedded/withMfa.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run withMfa retry loop (4 attempts repeatedly and assert the total submissions per issued code stay within the budget.
