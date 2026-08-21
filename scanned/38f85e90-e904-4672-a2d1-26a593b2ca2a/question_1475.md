# Q1475: entropyId is just the wallet address in session-signers.ts

## Question
getEntropyDetailsFromAccount uses the account address as the entropyId; can an attacker pass an address they merely know through addSessionSigners (getWallet then updateWallet with additional_signers.concat) and cause the iframe to load or recover the wrong wallet?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Call the provider path with a foreign address as entropyId.
- Invariant to test: Entropy identifiers must be validated against the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign address into addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert it is rejected before the proxy call.
