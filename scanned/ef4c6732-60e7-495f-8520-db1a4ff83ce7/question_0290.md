# Q0290: wallet_id lives only in the URL in sign-wallet-request.ts

## Question
The signed envelope includes the compiled url but the body omits wallet_id; can an attacker exploit URL/body separation in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so a signature produced for one wallet path is presented for another?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Compare envelopes for two wallet ids and test whether the server-visible binding is only positional.
- Invariant to test: Wallet identity must be bound inside the signed body as well as the path.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) includes wallet_id in the signed payload.
