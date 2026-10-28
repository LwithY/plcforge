PROGRAM PLC_PRG
VAR_INPUT
    FwdCmd : BOOL;
    RevCmd : BOOL;
    Stop : BOOL;
END_VAR
VAR_OUTPUT
    FwdRun : BOOL;
    RevRun : BOOL;
END_VAR
VAR

END_VAR
IF Stop OR (FwdCmd AND RevCmd) THEN
    FwdRun := FALSE;
    RevRun := FALSE;
ELSIF NOT FwdRun AND NOT RevRun THEN
    IF FwdCmd THEN
        FwdRun := TRUE;
    ELSIF RevCmd THEN
        RevRun := TRUE;
    END_IF;
END_IF;
END_PROGRAM
