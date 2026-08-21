# Q0683: refresh dedupe keyed by literal 'key' in Privy.ts

## Question
refreshSession dedupes in-flight refreshes in a Map keyed by the refresh token, falling back to the literal 'key' when none exists; can an attacker make two different sessions share one in-flight refresh promise?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Trigger simultaneous refreshes with no refresh token present in multi-user mode and observe the shared cache entry.
- Invariant to test: Concurrent refreshes for different identities must never share a result.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two refreshes for different users with absent refresh tokens and assert two distinct requests are issued.
