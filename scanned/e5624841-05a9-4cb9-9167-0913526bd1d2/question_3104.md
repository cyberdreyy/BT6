# Q3104: require_user_password_on_create bypass in UserApi.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through UserApi.get by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/client/UserApi.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call UserApi.get with each recoveryMethod, asserting the requirement holds.
