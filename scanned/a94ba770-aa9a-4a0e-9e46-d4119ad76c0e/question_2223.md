# Q2223: juplend_withdraw: withdraw refresh and redeem operate on different external state [repeated-deposit-withdraw-cycles-to] [recipient-binding]

## Question
Can an unprivileged attacker invoke `juplend_withdraw` with repeated deposit/withdraw cycles to probe asymmetry so `juplend_withdraw` refreshes one external state object but redeems another, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and leading to `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: repeated deposit/withdraw cycles to probe asymmetry
- Exploit idea: Where refresh precedes withdraw, ensure the refreshed reserve/obligation/position is exactly the one later burned or redeemed. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Feed mismatched external contexts and assert withdraw rejects unless refresh and redeem are bound to the same object. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
