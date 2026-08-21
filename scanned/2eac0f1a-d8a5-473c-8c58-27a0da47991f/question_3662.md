# Q3662: enrollment success not verified against refresh in MfaApi.ts

## Question
submitEnrollMfa returns the proxy result and then refreshes; can an attacker make MfaApi.verifyMfa report a successful enrollment that the server never recorded?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Return a success from the iframe path while the refresh shows no methods.
- Invariant to test: Reported enrollment success must be confirmed by the refreshed user state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return success with an empty mfa_methods refresh and assert MfaApi.verifyMfa reports failure.
