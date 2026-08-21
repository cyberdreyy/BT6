# Q2908: hex detection via loose regex in entropy.ts

## Question
The hex predicate accepts any 0x-prefixed hex string of any length, including empty; can an attacker exploit that in getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) so a zero-length or odd-length value is passed to the signer?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Submit '0x' and an odd-length hex string.
- Invariant to test: Hex inputs must be length-validated before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed '0x' and odd-length values to getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert rejection.
