# Q2551: acceptTerms mutates without confirmation in InMemoryStorage.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger InMemoryCache.get from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert InMemoryCache.get is not reachable from any automatic initialization path.
