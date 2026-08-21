# Q0288: wallet_id lives only in the URL in update-wallet.ts

## Question
The signed envelope includes the compiled url but the body omits wallet_id; can an attacker exploit URL/body separation in updateWallet(): signs {version:1 so a signature produced for one wallet path is presented for another?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Compare envelopes for two wallet ids and test whether the server-visible binding is only positional.
- Invariant to test: Wallet identity must be bound inside the signed body as well as the path.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert updateWallet(): signs {version:1 includes wallet_id in the signed payload.
