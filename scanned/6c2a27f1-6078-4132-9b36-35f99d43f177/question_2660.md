# Q2660: user.get returns refreshed foreign user in LocalStorage.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so LocalStorage.get (JSON.parse) returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert LocalStorage.get (JSON.parse) throws rather than returning the other profile.
