# Q3424: is_new_user drives privileged app UI in GuestApi.ts

## Question
GuestApi.create merges is_new_user and oauth_tokens from the authenticate response into the returned user; can an attacker influence those fields to make the integrating app treat an existing account as newly created?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Return is_new_user true for an existing account and observe the merged user object.
- Invariant to test: Merged response flags must be derived from the authenticated result, not accepted blindly.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert GuestApi.create derives is_new_user from the server result for the same subject as the stored token.
