PROGRAM PLC_PRG
VAR_INPUT
    Start : BOOL;
    Full : BOOL;
    Empty : BOOL;
    Abort : BOOL;
    Reset : BOOL;
END_VAR
VAR_OUTPUT
    State : INT;
    Fill : BOOL;
    Drain : BOOL;
    Done : BOOL;
END_VAR
VAR

END_VAR
IF Abort THEN
    State := 0;
ELSE
    CASE State OF
        0: IF Start THEN State := 1; END_IF;
        1: IF Full THEN State := 2; END_IF;
        2: IF Empty THEN State := 3; END_IF;
        3: IF Reset THEN State := 0; END_IF;
    ELSE
        State := 0;
    END_CASE;
END_IF;
Fill := State = 1;
Drain := State = 2;
Done := State = 3;
END_PROGRAM
