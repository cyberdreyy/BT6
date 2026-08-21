# Q1673: InMemoryCache prototype keys in Privy.ts

## Question
InMemoryCache stores entries on a plain object; can an attacker supply a key such as __proto__ or constructor through Privy constructor so a read returns an inherited value or a write corrupts the cache?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Call the storage put/get path with prototype-named keys.
- Invariant to test: Cache keys must not reach object prototype slots.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: put and get '__proto__' through Privy constructor and assert isolation.
