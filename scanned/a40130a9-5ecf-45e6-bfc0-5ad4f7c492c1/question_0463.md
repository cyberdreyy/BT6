# Q0463: null-key fallback serves the wrong user in Privy.ts

## Question
Because tokens are also written under the null key, can Privy constructor return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/client/Privy.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert Privy constructor does not return the null-keyed token of a different subject.
