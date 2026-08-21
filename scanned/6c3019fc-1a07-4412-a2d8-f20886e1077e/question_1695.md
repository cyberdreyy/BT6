# Q1695: imported wallets bypass the fallback in session-signers.ts

## Question
getEntropyDetailsFromUser returns the signing account directly when imported is set; can an attacker mark an account object as imported so addSessionSigners (getWallet then updateWallet with additional_signers.concat) derives entropy from an account of their choosing?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Pass a hand-built account with imported true.
- Invariant to test: Account flags used for entropy selection must come from server-confirmed data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert re-validation against the session user.
