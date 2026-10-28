# Scan semantics and interlock rules
Input values are sampled at scan start. Program statements execute in source order.
Outputs for scan k are the values after scan k completes. Internal variables and FB
instances persist across scans. Reset each independent test to declared initial state.
Stop and emergency inputs override Start when a task specifies stop priority.
Use IF Stop OR EStop THEN Motor := FALSE; ELSIF Start THEN Motor := TRUE; END_IF;
for a stop-priority latch. Absence of ELSE deliberately retains the state.
For mutually exclusive directions, compute one arbitration decision before outputs;
simultaneous opposite commands must follow the task's explicit priority rule.
Ordinary ST logic is not a certified safety controller. These examples are coursework
and offline logic; safety certification and actual physical interlocks need separate design.
