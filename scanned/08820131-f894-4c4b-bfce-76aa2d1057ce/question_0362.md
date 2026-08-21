# Q0362: four attempts amplify code guessing in MfaApi.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use MfaApi.verifyMfa to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/client/mfa/MfaApi.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run MfaApi.verifyMfa repeatedly and assert the total submissions per issued code stay within the budget.
