# Timer counter and edge instances
Declare an instance, e.g. delay : TON; edge : R_TRIG; count : CTU;
Call delay(IN := Enable, PT := T#100ms); then read delay.Q and delay.ET.
Call edge(CLK := Sensor); then read edge.Q. Call each instance exactly once every scan,
including scans that reset the block. Reinitializing an instance per scan destroys state.
CTU inputs are CU, RESET, PV. Read CV and Q. A held CU is one rising edge, not repeated
counts. RESET has priority over CU. Use an INT-compatible PV and consider saturation.
The local simulator treats the first TON true scan as ET=0; each subsequent consecutive
true scan adds dt_ms. TON false resets ET/Q. TOF false edge starts ET=0. TP starts at
ET=0 on a rising edge. This deterministic discrete-time approximation must be compared
with real CODESYS timing after installation; it is not proof of runtime timing behavior.
