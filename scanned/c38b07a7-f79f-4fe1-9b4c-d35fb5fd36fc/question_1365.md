# Q1365: entropyIdVerifier argument ignored in session-signers.ts

## Question
EmbeddedWalletApi.getEthereumProvider forwards the caller's entropyId but constructs the provider with a hardcoded 'ethereum-address-verifier'; can an attacker exploit that mismatch through addSessionSigners (getWallet then updateWallet with additional_signers.concat) so connect and rpc use inconsistent entropy identities?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Pass a solana verifier with an ethereum wallet and compare the connect and rpc payloads.
- Invariant to test: The entropy identity used to connect must be the identity used to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call addSessionSigners (getWallet then updateWallet with additional_signers.concat) with a non-default verifier and assert the same verifier reaches every proxy call.
