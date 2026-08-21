# Q0694: clearMfa after refresh sees zero methods in MfaPasskeyApi.ts

## Question
MfaApi calls proxy.clearMfa when the refreshed user reports mfa_methods.length === 0; can an attacker cause a stale or partial refresh so MfaPasskeyApi.generateAuthenticationOptions clears MFA state while methods still exist?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Return a refresh response with an empty mfa_methods array during an unrelated operation.
- Invariant to test: MFA state may only be cleared when the server authoritatively reports no methods for that user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an empty mfa_methods for a user that has methods and assert MfaPasskeyApi.generateAuthenticationOptions does not clear.
