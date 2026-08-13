# Q2216: juplend_withdraw: withdraw refresh and redeem operate on different external state [remaining-accounts-that-can-swap] [round-trip]

## Question
Can an unprivileged attacker invoke `juplend_withdraw` with remaining accounts that can swap rate update and withdraw market contexts so `juplend_withdraw` refreshes one external state object but redeems another, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and leading to `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: remaining accounts that can swap rate update and withdraw market contexts
- Exploit idea: Where refresh precedes withdraw, ensure the refreshed reserve/obligation/position is exactly the one later burned or redeemed. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Feed mismatched external contexts and assert withdraw rejects unless refresh and redeem are bound to the same object. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
