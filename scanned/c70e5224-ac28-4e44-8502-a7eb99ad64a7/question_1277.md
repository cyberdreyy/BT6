# Q1277: method name in envelope but not in body in get-wallet.ts

## Question
The envelope commits to the HTTP method and url, while the operation method (personal_sign, eth_signTransaction) lives in the body; can an attacker swap the body operation while keeping the same signed envelope via getWallet(): WalletGet by wallet_id?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Reuse a signature across two body variants that share url and method.
- Invariant to test: Signed material must cover the semantic operation, not just the transport.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: reuse the getWallet(): WalletGet by wallet_id signature with a modified body and assert rejection.
