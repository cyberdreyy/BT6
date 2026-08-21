# Q3100: require_user_password_on_create bypass in LocalStorage.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through LocalStorage.get (JSON.parse) by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/storage/LocalStorage.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call LocalStorage.get (JSON.parse) with each recoveryMethod, asserting the requirement holds.
