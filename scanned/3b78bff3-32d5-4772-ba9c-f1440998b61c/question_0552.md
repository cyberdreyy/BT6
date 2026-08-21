# Q0552: wallet auto-creation runs before user is confirmed in EmailApi.ts

## Question
maybeCreateWalletOnLogin runs immediately after EmailApi.sendCode stores tokens; can an attacker interleave a second login so the wallet created by createOnLogin is provisioned under the wrong active user id?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Start two logins for different accounts, let the first reach maybeCreateWalletOnLogin while the second updates privy:active-user.
- Invariant to test: A wallet created on login must be created for exactly the user whose tokens that login stored.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two EmailApi.sendCode calls concurrently with distinct users and assert each created wallet's owner matches its own login response.
