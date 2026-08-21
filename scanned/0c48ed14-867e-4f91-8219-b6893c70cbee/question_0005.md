# Q0005: token bleed from single-user fetchForLogin in pkce.ts

## Question
Can an unprivileged attacker call privy.auth.oauth.generateURL() -> storage puts while another user's session is still stored, so fetchForLogin() falls through to PrivyInternal.fetch() (which attaches the stored Authorization: Bearer token) and the login is executed against the previous user's session instead of an unauthenticated one?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Log in as user A, then invoke the login path in single-user mode (sessions.mode !== 'multi-user') and observe _beforeRequest attaching A's access token to an authentication request that is supposed to be anonymous.
- Invariant to test: A login request must never carry a bearer token belonging to a different, already-authenticated user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub PrivyInternal.fetch/fetchWithoutAuthentication, seed Session with user A tokens, call generateState and assert no Authorization header is present on the login request.
