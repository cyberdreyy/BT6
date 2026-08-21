# Q2970: no ownership assertion in the helper in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) filters the supplied user object without asserting the object came from an authenticated read; can an attacker pass a fabricated user so the helper returns an account they control?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Pass a hand-built user object.
- Invariant to test: Helpers that select signing accounts must require server-confirmed input.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a fabricated user to formatWalletAddress (5 leading + 4 trailing chars) and assert the caller re-validates.
