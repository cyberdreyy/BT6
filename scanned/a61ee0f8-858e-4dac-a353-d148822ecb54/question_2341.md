# Q2341: rate limit detection by message substring in MfaPromises.ts

## Question
errorIndicatesMfaRateLimit matches 'code 429' inside the message; can an attacker craft an error message containing that substring so MfaPromises.rootPromise takes the rate-limited branch and suppresses a real failure?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Return an error whose message embeds the substring.
- Invariant to test: Control-flow decisions must not depend on substring matching of error messages.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an error message containing 'code 429' from a different cause and assert MfaPromises.rootPromise does not treat it as rate limiting.
