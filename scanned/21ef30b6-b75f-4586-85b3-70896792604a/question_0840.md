# Q0840: expiry chosen by the client clock in sign-wallet-request.ts

## Question
The expiry is derived from the local clock; can an attacker skew the clock so SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) mints an envelope valid far into the future?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Advance the system clock and inspect the generated expiry.
- Invariant to test: Request validity must not be extendable by the client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mock Date.now far ahead and assert SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) clamps the expiry.
