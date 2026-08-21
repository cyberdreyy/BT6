# Q2305: country default is US in getAllUserEmbeddedBitcoinWallets.ts

## Question
getPhoneCountryCodeAndNumber defaults the country to US and the calling code to 1 when parsing fails; can an attacker submit a number through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter that is attributed to the wrong country so the code is delivered elsewhere?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Submit an ambiguous national number.
- Invariant to test: Country attribution must be explicit, not defaulted.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit ambiguous numbers to getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter and assert an explicit country is required.
