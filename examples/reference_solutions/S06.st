PROGRAM PLC_PRG
VAR_INPUT
    Enable : BOOL;
    EStop : BOOL;
END_VAR
VAR_OUTPUT
    Ready : BOOL;
END_VAR
VAR
    Delay : TON;
END_VAR
Delay(IN := Enable AND NOT EStop, PT := T#60ms);
Ready := Delay.Q AND NOT EStop;
END_PROGRAM
