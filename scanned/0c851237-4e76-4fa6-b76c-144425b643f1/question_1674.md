# Q1674: InMemoryCache prototype keys in UserApi.ts

## Question
InMemoryCache stores entries on a plain object; can an attacker supply a key such as __proto__ or constructor through UserApi.get so a read returns an inherited value or a write corrupts the cache?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Call the storage put/get path with prototype-named keys.
- Invariant to test: Cache keys must not reach object prototype slots.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: put and get '__proto__' through UserApi.get and assert isolation.
