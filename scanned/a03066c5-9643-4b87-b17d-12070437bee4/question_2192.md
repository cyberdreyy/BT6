# Q2192: juplend_withdraw: withdraw releases more value than the external position burned [repeated-deposit-withdraw-cycles-to] [round-trip]

## Question
Can an unprivileged attacker call `juplend_withdraw` with repeated deposit/withdraw cycles to probe asymmetry so `juplend_withdraw` releases more value than the corresponding external position actually burned, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: repeated deposit/withdraw cycles to probe asymmetry
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
