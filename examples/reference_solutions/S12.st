PROGRAM PLC_PRG
VAR_INPUT
    A : BOOL;
    B : BOOL;
    C : BOOL;
    Enable : BOOL;
    EStop : BOOL;
END_VAR
VAR_OUTPUT
    Vote : BOOL;
    Disagree : BOOL;
    SafeOut : BOOL;
END_VAR
VAR

END_VAR
Vote := Enable AND ((A AND B) OR (A AND C) OR (B AND C));
Disagree := NOT ((A = B) AND (B = C));
SafeOut := Vote AND NOT EStop;
END_PROGRAM
