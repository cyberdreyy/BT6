# Q0363: four attempts amplify code guessing in MfaSmsApi.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use MfaSmsApi.sendCode to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/client/mfa/MfaSmsApi.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run MfaSmsApi.sendCode repeatedly and assert the total submissions per issued code stay within the budget.
