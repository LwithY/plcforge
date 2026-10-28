# LTL requirements and scan tests
Use explicit interface names. After-scan convention: G ((StopBtn | EStop) -> !Motor).
Use X when explicitly referring to the next sampled scan: G (Motor -> X (...)).
Finite traces cannot establish universal unbounded correctness. The built-in monitor
reports no_violation_observed, violated, inconclusive, or unsupported. It is not PLCVerif.
Cover initial state, nominal behavior, held inputs, simultaneous priority conflicts,
reset recovery, thresholds and bounds. Test expected values are the requirement oracle;
do not compute them from the candidate program or weaken them to make the program pass.
In evaluation, preserve all original tasks and tests. Report unsupported and unavailable
separately. Count compilation only from real CODESYS. Compare frameworks using identical
models, task prompts, tool budgets, compiler target, repetition count and tests.
