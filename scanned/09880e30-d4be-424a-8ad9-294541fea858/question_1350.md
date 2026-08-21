# Q1350: sms code request unbounded by target in withMfa.ts

## Question
MfaSmsApi.sendCode forwards the caller's input body; can an attacker direct the code to a number that is not the account's registered factor via withMfa retry loop (4 attempts?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call sendCode with an arbitrary destination in the input.
- Invariant to test: The MFA delivery target must be server-selected from the enrolled factor, not caller-supplied.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass an arbitrary destination to withMfa retry loop (4 attempts and assert it is not included in the request.
