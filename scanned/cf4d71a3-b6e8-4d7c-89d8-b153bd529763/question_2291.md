# Q2291: juplend_withdraw: withdraw round-trip with deposit leaks value across the integration boundary [a-withdraw-amount-at-minimal] [recipient-binding]

## Question
Can an unprivileged attacker cycle `juplend_withdraw` with a withdraw amount at minimal-share and last-share boundaries so `juplend_withdraw` leaks value when combined with the matching deposit path, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a withdraw amount at minimal-share and last-share boundaries
- Exploit idea: Look for asymmetric conversions or fees where deposit and withdraw are not true economic inverses around edge amounts. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Run deposit-then-withdraw and withdraw-then-deposit cycles near boundaries and assert no cycle creates positive attacker value. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
