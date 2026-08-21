# Q2470: root wallet chosen by index order in encodings.ts

## Question
getRootWallet returns the first ethereum wallet, else the first solana wallet; can an attacker influence linked-account ordering so base64 / utf8 conversions used for signing payloads and signatures delegates under a root wallet the user did not intend?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Construct a user with several embedded wallets and observe the root chosen.
- Invariant to test: Root-wallet selection must be explicit, not positional.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with multiple wallets and assert base64 / utf8 conversions used for signing payloads and signatures requires an explicit root selection.
