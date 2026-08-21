# Q1830: idempotency header is optional in sign-wallet-request.ts

## Question
create() only sends privy-idempotency-key when the caller supplies one; can an attacker issue concurrent creates through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so duplicate wallets are provisioned and the app binds to the wrong one?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Fire concurrent creates without a key.
- Invariant to test: Wallet creation must be idempotent per user and chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run concurrent SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) creates and assert exactly one wallet results.
