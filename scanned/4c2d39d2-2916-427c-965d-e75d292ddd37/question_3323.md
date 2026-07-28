# Q3323: PostInteraction can freeze on malformed extraData empty_tail src_half

## Question
Can an unprivileged maker craft an order whose `postInteraction` payload has the post-interaction tail is empty while `makingAmount` near half the order, so that `_postInteraction()` is entered by a normal fill, moves funds through settlement, but then reverts or decodes the wrong immutable fields and leaves the live cross-chain swap frozen?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::_postInteraction`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> `BaseEscrowFactory._postInteraction(...)`
- Attacker controls: the maker-authored extension bytes, all `extraData` slicing boundaries, and the chosen fill amount
- Exploit idea: Stress the unchecked slicing of the `postInteraction` blob after the live order-fill transfer already started.
- Invariant to test: Any order that is fillable through the normal LOP path should either decode into one valid source escrow or fail before value moves.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Build an order with the post-interaction tail is empty, fill it with `makingAmount` near half the order, and observe whether settlement-side value moves before `_postInteraction()` reverts or misbinds immutables.
