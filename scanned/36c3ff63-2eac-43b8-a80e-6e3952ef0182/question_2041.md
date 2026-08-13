# Q2041: juplend_deposit: deposit can initialize a toxic integration state for later theft [a-deposit-followed-immediately-by] [owner-binding]

## Question
Can an unprivileged attacker use `juplend_deposit` with a deposit followed immediately by borrow or withdraw investigation path so `juplend_deposit` creates a valid-looking but toxic integration state that later enables `Critical: phantom collateral credit or redirected external position` by violating `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only`? Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: a deposit followed immediately by borrow or withdraw investigation path
- Exploit idea: Look for one-time initialization or first-deposit side effects that later withdrawals/harvests trust blindly. Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: After creating the controlled initial state, execute the dependent follow-on action and assert no later path can extract or misroute value. Deposit under mixed owner and position contexts and assert every resulting derivative or reward path still belongs to the canonical owner.
