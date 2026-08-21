# Q3015: solana rpc path only implements signMessage in session-signers.ts

## Question
walletRpc's solana branch handles signMessage and returns undefined for anything else; can an attacker exploit the undefined return in addSessionSigners (getWallet then updateWallet with additional_signers.concat) so a caller treats a failed operation as success?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Call an unsupported solana method and inspect the resolved value.
- Invariant to test: Unsupported operations must reject rather than resolve undefined.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call an unsupported method through addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert it rejects.
