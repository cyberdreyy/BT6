# Q2798: eth_sign and secp256k1_sign share a path in entropy.ts

## Question
walletRpc maps eth_sign and secp256k1_sign to the same raw hash signing method; can an attacker use getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) to obtain a raw-hash signature over a value the user believed was a display message?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Submit a 32-byte hash-shaped string through the message path.
- Invariant to test: Raw hash signing must be visibly distinct from message signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) refuses raw-hash signing without an explicit raw-sign intent.
