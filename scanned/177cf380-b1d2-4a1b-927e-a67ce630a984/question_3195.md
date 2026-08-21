# Q3195: clearMfa keyed by caller-supplied userId in pkce.ts

## Question
AuthApi.logout forwards opts.userId to mfa.clearMfa; can an attacker pass another user's id and clear MFA state that is not theirs?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Call logout with a foreign userId and observe the proxy clearMfa invocation.
- Invariant to test: MFA state may only be cleared for the currently authenticated user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call generateState with a foreign userId and assert clearMfa is called with the session's own user id or not at all.
