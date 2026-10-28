PROGRAM PLC_PRG
VAR_INPUT
    Enable : BOOL;
    Level : REAL;
END_VAR
VAR_OUTPUT
    Pump : BOOL;
    Fault : BOOL;
END_VAR
VAR

END_VAR
Fault := (Level < 0.0) OR (Level > 100.0);
IF NOT Enable OR Fault THEN
    Pump := FALSE;
ELSIF Level <= 20.0 THEN
    Pump := TRUE;
ELSIF Level >= 80.0 THEN
    Pump := FALSE;
END_IF;
END_PROGRAM
