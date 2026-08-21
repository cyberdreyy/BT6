# Q1278: method name in envelope but not in body in update-wallet.ts

## Question
The envelope commits to the HTTP method and url, while the operation method (personal_sign, eth_signTransaction) lives in the body; can an attacker swap the body operation while keeping the same signed envelope via updateWallet(): signs {version:1?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Reuse a signature across two body variants that share url and method.
- Invariant to test: Signed material must cover the semantic operation, not just the transport.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: reuse the updateWallet(): signs {version:1 signature with a modified body and assert rejection.
