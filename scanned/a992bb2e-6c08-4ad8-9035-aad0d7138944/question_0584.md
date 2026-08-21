# Q0584: mfaRequired event carries no operation identity in MfaPasskeyApi.ts

## Question
The 'mfaRequired' event emitted from src/client/mfa/MfaPasskeyApi.ts does not identify which operation triggered it; can an attacker exploit this so the app collects a code for the wrong pending action?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Trigger two operations and inspect the event payload the app receives.
- Invariant to test: MFA prompts must be attributable to the exact operation awaiting them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert the event payload emitted during MfaPasskeyApi.generateAuthenticationOptions identifies the pending operation.
