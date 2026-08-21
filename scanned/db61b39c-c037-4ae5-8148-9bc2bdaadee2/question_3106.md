# Q3106: require_user_password_on_create bypass in logger.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through logger levels NONE/ERROR/WARN/INFO/DEBUG by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/client/logger.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call logger levels NONE/ERROR/WARN/INFO/DEBUG with each recoveryMethod, asserting the requirement holds.
