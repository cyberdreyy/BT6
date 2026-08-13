# Q2202: juplend_withdraw: withdraw targets the wrong recipient or vault authority [a-withdraw-immediately-after-reward] [round-trip]

## Question
Can an unprivileged attacker use `juplend_withdraw` with a withdraw immediately after reward or rate-related public maintenance so `juplend_withdraw` sends withdrawn assets to the wrong recipient or through the wrong vault authority, breaking `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a withdraw immediately after reward or rate-related public maintenance
- Exploit idea: Probe destination binding and PDA authority checks across the final transfer-out phase. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Swap destinations and authorities in the controlled setup and assert no accepted path transfers value to an unvalidated account. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
