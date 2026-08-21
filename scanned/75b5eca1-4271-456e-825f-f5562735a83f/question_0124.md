# Q0124: partial token write leaves mixed identity in GuestApi.ts

## Question
In src/client/auth/GuestApi.ts, can an attacker force one of the four writes in Session.updateWithTokensResponse (customer access token, privy access token, refresh token, identity token) to fail after GuestApi.create succeeds, leaving storage holding user B's access token next to user A's refresh token?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Trigger the login, make one storage key unwritable (quota/serialization), and observe the 'error_storing_tokens' path returning early after some tokens were already persisted.
- Invariant to test: Token storage after a login is all-or-nothing: no combination of keys may name two different subjects.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test with a Storage stub that rejects on privy:refresh_token; call GuestApi.create and assert no residual privy:token from the new response remains.
