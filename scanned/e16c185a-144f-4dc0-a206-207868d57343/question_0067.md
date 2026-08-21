# Q0067: unsigned headers appended after signing in get-wallet.ts

## Question
rpc() signs an envelope containing only privy-app-id and privy-request-expiry, then spreads the caller's extraHeaders after the signature header; can an unprivileged attacker pass headers through addSessionSigners read-modify-write that are transmitted but not covered by the authorization signature, or that overwrite the signature header itself?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Call the wallet RPC path with an extraHeaders object containing privy-authorization-signature and privy-request-expiry and inspect the outgoing request.
- Invariant to test: Every header that influences server-side authorization must be inside the signed envelope and immutable afterwards.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call getWallet(): WalletGet by wallet_id with crafted extraHeaders and assert the final headers equal the signed set.
