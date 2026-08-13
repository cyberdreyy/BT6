# Q1460: kamino_withdraw: withdraw intermediary flow can be replayed or interrupted [a-withdraw-amount-at-last] [round-trip]

## Question
Can an unprivileged attacker make `kamino_withdraw` drive `kamino_withdraw` with a withdraw amount at last-share and tiny redemption boundaries so an intermediary withdraw flow can be replayed, interrupted, or finalized twice, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a withdraw amount at last-share and tiny redemption boundaries
- Exploit idea: Audit multi-hop withdraws that pass through temporary ATAs or protocol-owned accounts before reaching the user. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Replay or fail at each hop and assert no hop can be repeated or left value-bearing without a single canonical finalization. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
