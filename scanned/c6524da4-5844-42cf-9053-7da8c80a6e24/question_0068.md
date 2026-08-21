# Q0068: unsigned headers appended after signing in update-wallet.ts

## Question
rpc() signs an envelope containing only privy-app-id and privy-request-expiry, then spreads the caller's extraHeaders after the signature header; can an unprivileged attacker pass headers through session signer add/remove that are transmitted but not covered by the authorization signature, or that overwrite the signature header itself?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Call the wallet RPC path with an extraHeaders object containing privy-authorization-signature and privy-request-expiry and inspect the outgoing request.
- Invariant to test: Every header that influences server-side authorization must be inside the signed envelope and immutable afterwards.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call updateWallet(): signs {version:1 with crafted extraHeaders and assert the final headers equal the signed set.
