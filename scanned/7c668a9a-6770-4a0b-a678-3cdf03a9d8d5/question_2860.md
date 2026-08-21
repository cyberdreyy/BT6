# Q2860: selection used to authorise operations in formatters.ts

## Question
Callers frequently pass the result of formatWalletAddress (5 leading + 4 trailing chars) straight into signing and delegation calls; can an attacker exploit the absence of a re-check so an account chosen at render time authorises an action later?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Select an account, change the session, then act.
- Invariant to test: Authorisation must re-derive the account at action time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change the session between selection from formatWalletAddress (5 leading + 4 trailing chars) and the action, and assert refusal.
