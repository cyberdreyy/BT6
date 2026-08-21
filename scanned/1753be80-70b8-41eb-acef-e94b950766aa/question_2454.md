# Q2454: mfa cancelled treated as success in MfaPasskeyApi.ts

## Question
errorIndicatesMfaCanceled checks error.code === 'mfa_canceled'; can an attacker make MfaPasskeyApi.generateAuthenticationOptions treat a cancellation as a benign outcome so the calling app proceeds as if the operation was authorised?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Cancel an MFA prompt mid-operation and inspect what the operation returns.
- Invariant to test: A cancelled MFA must produce a failure the app cannot mistake for approval.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cancel during MfaPasskeyApi.generateAuthenticationOptions and assert the returned promise rejects.
