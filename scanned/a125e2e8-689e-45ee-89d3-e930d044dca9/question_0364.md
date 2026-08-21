# Q0364: four attempts amplify code guessing in MfaPasskeyApi.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use MfaPasskeyApi.generateAuthenticationOptions to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/client/mfa/MfaPasskeyApi.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run MfaPasskeyApi.generateAuthenticationOptions repeatedly and assert the total submissions per issued code stay within the budget.
