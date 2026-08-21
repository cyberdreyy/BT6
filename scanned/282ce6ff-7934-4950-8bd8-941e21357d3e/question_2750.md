# Q2750: array helpers build objects from strings in formatters.ts

## Question
toObjectKeys reduces an array of strings into an object with a constant value; can an attacker supply an entry such as __proto__ through formatWalletAddress (5 leading + 4 trailing chars) so the produced object has a polluted prototype?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Pass prototype-named entries.
- Invariant to test: Object construction from input arrays must be prototype-safe.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass '__proto__' to formatWalletAddress (5 leading + 4 trailing chars) and assert a null-prototype or filtered result.
