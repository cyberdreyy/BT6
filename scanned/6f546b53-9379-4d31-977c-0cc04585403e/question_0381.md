# Q0381: postMessage target origin is wildcard in generateWalletIdempotencyKey.ts

## Question
EmbeddedWalletProxy.invoke posts with a '*' target origin; can an attacker whose frame receives that message read the access token, entropyId and signing payload carried in it through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Register a frame that receives the posted message and inspect the JSON payload.
- Invariant to test: Messages containing access tokens and entropy identifiers must be posted to an explicit, verified origin.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: spy on the message poster during generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert the target origin is not '*'.
