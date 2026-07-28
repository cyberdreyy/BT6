# Q3382: PostInteraction can freeze on malformed extraData bad_timelocks src_small

## Question
Can an unprivileged maker craft an order whose `postInteraction` payload has the encoded timelocks blob is malformed while `makingAmount` just above zero, so that `_postInteraction()` is entered by a normal fill, moves funds through settlement, but then reverts or decodes the wrong immutable fields and leaves the live cross-chain swap frozen?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::_postInteraction`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> `BaseEscrowFactory._postInteraction(...)`
- Attacker controls: the maker-authored extension bytes, all `extraData` slicing boundaries, and the chosen fill amount
- Exploit idea: Stress the unchecked slicing of the `postInteraction` blob after the live order-fill transfer already started.
- Invariant to test: Any order that is fillable through the normal LOP path should either decode into one valid source escrow or fail before value moves.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Build an order with the encoded timelocks blob is malformed, fill it with `makingAmount` just above zero, and observe whether settlement-side value moves before `_postInteraction()` reverts or misbinds immutables.
