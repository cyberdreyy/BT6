# Q2894: verifyMfa reachable without a pending operation in MfaPasskeyApi.ts

## Question
MfaApi.verifyMfa can be invoked directly; can an attacker call MfaPasskeyApi.generateAuthenticationOptions to consume an MFA code outside any operation, leaving a satisfied MFA state that a later operation reuses?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call verifyMfa alone, then immediately start a signing operation.
- Invariant to test: An MFA verification must be consumed by the operation that required it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call MfaPasskeyApi.generateAuthenticationOptions then a signature and assert the signature still requires its own MFA round.
