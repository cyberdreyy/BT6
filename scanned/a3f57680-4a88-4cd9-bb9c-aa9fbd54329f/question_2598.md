# Q2598: chain_type chosen by the caller in update-wallet.ts

## Question
The signed body includes a caller-supplied chain_type; can an attacker mismatch chain_type against the wallet through updateWallet(): signs {version:1 so a signature valid on one chain is produced for a wallet on another?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Submit an ethereum method for a solana wallet id.
- Invariant to test: Chain type must be derived from the wallet record, not the request.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: mismatch chain_type and wallet in updateWallet(): signs {version:1 and assert rejection.
