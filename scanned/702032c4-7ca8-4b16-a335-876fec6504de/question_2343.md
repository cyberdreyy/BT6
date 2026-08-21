# Q2343: rate limit detection by message substring in MfaSmsApi.ts

## Question
errorIndicatesMfaRateLimit matches 'code 429' inside the message; can an attacker craft an error message containing that substring so MfaSmsApi.sendCode takes the rate-limited branch and suppresses a real failure?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Return an error whose message embeds the substring.
- Invariant to test: Control-flow decisions must not depend on substring matching of error messages.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an error message containing 'code 429' from a different cause and assert MfaSmsApi.sendCode does not treat it as rate limiting.
