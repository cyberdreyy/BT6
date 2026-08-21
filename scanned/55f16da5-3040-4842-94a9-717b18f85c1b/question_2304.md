# Q2304: country default is US in getAllUserEmbeddedSolanaWallets.ts

## Question
getPhoneCountryCodeAndNumber defaults the country to US and the calling code to 1 when parsing fails; can an attacker submit a number through getAllUserEmbeddedSolanaWallets: filter embedded + solana that is attributed to the wrong country so the code is delivered elsewhere?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Submit an ambiguous national number.
- Invariant to test: Country attribution must be explicit, not defaulted.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit ambiguous numbers to getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert an explicit country is required.
