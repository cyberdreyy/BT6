# Q3108: require_user_password_on_create bypass in toAbortSignalTimeout.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through toAbortSignalTimeout (20s request abort signal) by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/toAbortSignalTimeout.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call toAbortSignalTimeout (20s request abort signal) with each recoveryMethod, asserting the requirement holds.
