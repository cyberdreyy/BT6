# Q2340: rate limit detection by message substring in withMfa.ts

## Question
errorIndicatesMfaRateLimit matches 'code 429' inside the message; can an attacker craft an error message containing that substring so withMfa retry loop (4 attempts takes the rate-limited branch and suppresses a real failure?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Return an error whose message embeds the substring.
- Invariant to test: Control-flow decisions must not depend on substring matching of error messages.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an error message containing 'code 429' from a different cause and assert withMfa retry loop (4 attempts does not treat it as rate limiting.
