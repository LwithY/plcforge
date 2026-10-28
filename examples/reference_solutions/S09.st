PROGRAM PLC_PRG
VAR_INPUT
    Raw : BOOL;
    Reset : BOOL;
END_VAR
VAR_OUTPUT
    Stable : BOOL;
    Count : INT;
END_VAR
VAR

END_VAR
IF Reset THEN
    Stable := FALSE;
    Count := 0;
ELSIF Raw = Stable THEN
    Count := 0;
ELSE
    Count := Count + 1;
    IF Count >= 3 THEN
        Stable := Raw;
        Count := 0;
    END_IF;
END_IF;
END_PROGRAM
