# Q0691: clearMfa after refresh sees zero methods in MfaPromises.ts

## Question
MfaApi calls proxy.clearMfa when the refreshed user reports mfa_methods.length === 0; can an attacker cause a stale or partial refresh so MfaPromises.rootPromise clears MFA state while methods still exist?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Return a refresh response with an empty mfa_methods array during an unrelated operation.
- Invariant to test: MFA state may only be cleared when the server authoritatively reports no methods for that user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an empty mfa_methods for a user that has methods and assert MfaPromises.rootPromise does not clear.
