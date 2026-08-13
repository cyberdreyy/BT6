# Q2250: juplend_withdraw: withdraw accepts attacker-shaped optional accounts at closeout [a-withdraw-immediately-after-reward] [round-trip]

## Question
Can an unprivileged attacker use `juplend_withdraw` with a withdraw immediately after reward or rate-related public maintenance so `juplend_withdraw` accepts attacker-shaped optional accounts during closeout, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a withdraw immediately after reward or rate-related public maintenance
- Exploit idea: Probe optional reward, mint, reserve, or destination accounts used only during withdraw and therefore easy to under-validate. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Supply valid-looking optional accounts from another context and assert withdraw never succeeds against them. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
