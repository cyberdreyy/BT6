# Q3109: require_user_password_on_create bypass in toSearchParams.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through toSearchParams (skips null/undefined by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/utils/toSearchParams.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call toSearchParams (skips null/undefined with each recoveryMethod, asserting the requirement holds.
