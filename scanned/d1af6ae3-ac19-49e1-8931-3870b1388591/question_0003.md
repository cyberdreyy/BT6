# Q0003: token bleed from single-user fetchForLogin in PhoneApi.ts

## Question
Can an unprivileged attacker call privy.auth.phone.loginWithCode(phone, code) while another user's session is still stored, so fetchForLogin() falls through to PrivyInternal.fetch() (which attaches the stored Authorization: Bearer token) and the login is executed against the previous user's session instead of an unauthenticated one?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Log in as user A, then invoke the login path in single-user mode (sessions.mode !== 'multi-user') and observe _beforeRequest attaching A's access token to an authentication request that is supposed to be anonymous.
- Invariant to test: A login request must never carry a bearer token belonging to a different, already-authenticated user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub PrivyInternal.fetch/fetchWithoutAuthentication, seed Session with user A tokens, call PhoneApi.sendCode and assert no Authorization header is present on the login request.
