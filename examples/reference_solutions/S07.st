PROGRAM PLC_PRG
VAR_INPUT
    Run : BOOL;
    Motion : BOOL;
    Reset : BOOL;
END_VAR
VAR_OUTPUT
    Alarm : BOOL;
    BadScans : INT;
END_VAR
VAR

END_VAR
IF Reset THEN
    BadScans := 0;
    Alarm := FALSE;
ELSE
    IF Run AND NOT Motion THEN
        IF BadScans < 3 THEN BadScans := BadScans + 1; END_IF;
    ELSE
        BadScans := 0;
    END_IF;
    IF BadScans >= 3 THEN Alarm := TRUE; END_IF;
END_IF;
END_PROGRAM
