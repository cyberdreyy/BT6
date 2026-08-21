# Q0234: legacy null-keyed copy outlives its user in GuestApi.ts

## Question
Can an attacker exploit the fact that GuestApi.create stores tokens both under privy:<userId>:token and the legacy null-keyed privy:token, so a later logout or user switch clears one copy and leaves the other usable?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Log in as A, log in as B in multi-user mode, then remove B and read the null-keyed key still holding a live credential.
- Invariant to test: Every stored credential copy must be invalidated together with the session it belongs to.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run GuestApi.create for user A then user B, call Session.destroyLocalState and assert getKeys() contains no privy:*token entries.
