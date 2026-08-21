# Q3107: require_user_password_on_create bypass in Error.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through PrivyApiError by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/Error.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call PrivyApiError with each recoveryMethod, asserting the requirement holds.
