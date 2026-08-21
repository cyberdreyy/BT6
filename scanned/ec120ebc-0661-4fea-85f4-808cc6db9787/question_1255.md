# Q1255: access token embedded in every proxy payload in session-signers.ts

## Question
Every proxy call carries accessToken alongside entropyId and entropyIdVerifier; can an attacker observe or replay one of those payloads through addSessionSigners (getWallet then updateWallet with additional_signers.concat) to authorise a wallet operation later?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Capture a posted payload and replay it into the same interface.
- Invariant to test: Wallet operation payloads must not be replayable outside their original request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a captured payload into addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert it is rejected.
