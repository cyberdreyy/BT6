# Q3101: require_user_password_on_create bypass in InMemoryStorage.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through InMemoryCache.get by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/storage/InMemoryStorage.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call InMemoryCache.get with each recoveryMethod, asserting the requirement holds.
