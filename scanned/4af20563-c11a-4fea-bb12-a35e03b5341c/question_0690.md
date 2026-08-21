# Q0690: clearMfa after refresh sees zero methods in withMfa.ts

## Question
MfaApi calls proxy.clearMfa when the refreshed user reports mfa_methods.length === 0; can an attacker cause a stale or partial refresh so withMfa retry loop (4 attempts clears MFA state while methods still exist?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Return a refresh response with an empty mfa_methods array during an unrelated operation.
- Invariant to test: MFA state may only be cleared when the server authoritatively reports no methods for that user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an empty mfa_methods for a user that has methods and assert withMfa retry loop (4 attempts does not clear.
